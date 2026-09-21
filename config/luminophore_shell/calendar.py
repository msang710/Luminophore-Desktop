from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets as random_secrets
import subprocess
import time
from typing import Callable, Protocol, Sequence
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import requests

from .network_policy import OfflineError, OfflinePolicy


TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class CalendarError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarAccount:
    account_id: str
    email: str
    calendar_id: str = "primary"


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str
    auth_uri: str = AUTH_URL
    token_uri: str = TOKEN_URL


def oauth_client_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "luminophore-shell/google-oauth-client.json"


def _decode_oauth_client(data: str) -> OAuthClient:
    try:
        raw = json.loads(data)
        installed = raw["installed"]
        client = OAuthClient(
            str(installed["client_id"]), str(installed.get("client_secret", "")),
            str(installed.get("auth_uri", AUTH_URL)), str(installed.get("token_uri", TOKEN_URL)),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CalendarError("google_oauth_client_invalid") from exc
    if not client.client_id or not client.auth_uri.startswith("https://") or not client.token_uri.startswith("https://"):
        raise CalendarError("google_oauth_client_invalid")
    return client


def load_oauth_client(path: Path) -> OAuthClient:
    try:
        if path.stat().st_mode & 0o077:
            raise CalendarError("google_oauth_client_permissions_must_be_0600")
        return _decode_oauth_client(path.read_text(encoding="utf-8"))
    except CalendarError:
        raise
    except OSError as exc:
        raise CalendarError("google_oauth_client_missing") from exc


def install_oauth_client(source: Path, destination: Path | None = None) -> OAuthClient:
    target = destination or oauth_client_path()
    try:
        data = source.read_text(encoding="utf-8")
        client = _decode_oauth_client(data)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        target.chmod(0o600)
        return client
    except CalendarError:
        raise
    except OSError as exc:
        raise CalendarError("google_oauth_client_install_failed") from exc


def calendar_error_message(error: str) -> str:
    messages = {
        "google_oauth_client_missing": "Google OAuth JSON 파일을 선택해 주세요.",
        "google_oauth_client_invalid": "Desktop app 형식의 Google OAuth JSON이 아닙니다.",
        "google_oauth_client_permissions_must_be_0600": "Google OAuth JSON 권한은 0600이어야 합니다.",
        "google_oauth_client_install_failed": "Google OAuth JSON을 안전하게 설치하지 못했습니다.",
        "google_oauth_browser_open_failed": "Google 로그인 브라우저를 열지 못했습니다.",
        "google_oauth_denied": "Google Calendar 권한 요청이 취소되었습니다.",
        "google_oauth_callback_invalid": "Google 로그인 응답을 확인하지 못했습니다.",
        "google_oauth_refresh_token_missing": "Google에서 장기 연결 토큰을 반환하지 않았습니다.",
        "calendar_account_not_connected": "Google Calendar가 연결되지 않았습니다.",
    }
    return messages.get(error, error)


def pkce_pair() -> tuple[str, str]:
    verifier = random_secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(client: OAuthClient, redirect_uri: str, state: str, challenge: str) -> str:
    return client.auth_uri + "?" + urlencode({
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": CALENDAR_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


class GoogleOAuthFlow:
    def __init__(
        self,
        client: OAuthClient,
        post: Callable[..., object] = requests.post,
        open_browser: Callable[[str], object] = webbrowser.open,
    ) -> None:
        self.client = client
        self.post = post
        self.open_browser = open_browser

    def run(self, timeout_seconds: float = 180) -> TokenBundle:
        result: dict[str, str] = {}
        expected_state = random_secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                query = parse_qs(urlparse(self.path).query)
                result["state"] = query.get("state", [""])[0]
                result["code"] = query.get("code", [""])[0]
                result["error"] = query.get("error", [""])[0]
                body = "Google Calendar 연결을 완료했습니다. 이 창을 닫아도 됩니다.".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Callback)
        server.timeout = timeout_seconds
        redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2callback"
        try:
            if not self.open_browser(authorization_url(self.client, redirect_uri, expected_state, challenge)):
                raise CalendarError("google_oauth_browser_open_failed")
            server.handle_request()
        finally:
            server.server_close()
        if result.get("error"):
            raise CalendarError("google_oauth_denied")
        if result.get("state") != expected_state or not result.get("code"):
            raise CalendarError("google_oauth_callback_invalid")
        response = self.post(
            self.client.token_uri,
            data={
                "code": result["code"], "client_id": self.client.client_id,
                "client_secret": self.client.client_secret, "redirect_uri": redirect_uri,
                "grant_type": "authorization_code", "code_verifier": verifier,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        refresh_token = str(payload.get("refresh_token", ""))
        if not refresh_token:
            raise CalendarError("google_oauth_refresh_token_missing")
        return TokenBundle(
            str(payload["access_token"]), refresh_token,
            time.time() + int(payload.get("expires_in", 3600)),
        )


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    summary: str
    start: str
    end: str
    all_day: bool


@dataclass(frozen=True)
class CalendarSnapshot:
    account_id: str
    fetched_at: float
    events: tuple[CalendarEvent, ...]
    stale: bool = False
    error: str = ""


class SecretStore(Protocol):
    def lookup(self, kind: str, account_id: str) -> str: ...
    def store(self, kind: str, account_id: str, value: str) -> None: ...
    def clear(self, kind: str, account_id: str) -> None: ...


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _secret_run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=10, **kwargs)


class SecretToolStore:
    def __init__(self, runner: Runner = _secret_run) -> None:
        self.runner = runner

    @staticmethod
    def _attributes(kind: str, account_id: str) -> tuple[str, ...]:
        return ("service", "luminophore-shell", "kind", kind, "account", account_id)

    def lookup(self, kind: str, account_id: str) -> str:
        result = self.runner(("secret-tool", "lookup", *self._attributes(kind, account_id)))
        if result.returncode != 0:
            return ""
        return result.stdout.rstrip("\n")

    def store(self, kind: str, account_id: str, value: str) -> None:
        result = self.runner(
            ("secret-tool", "store", f"--label=Luminophore Shell {kind}", *self._attributes(kind, account_id)),
            input=value,
        )
        if result.returncode != 0:
            raise CalendarError("secret_service_store_failed")

    def clear(self, kind: str, account_id: str) -> None:
        result = self.runner(("secret-tool", "clear", *self._attributes(kind, account_id)))
        if result.returncode not in {0, 1}:
            raise CalendarError("secret_service_clear_failed")


def _token_from_json(raw: str) -> TokenBundle | None:
    try:
        value = json.loads(raw)
        return TokenBundle(str(value["access_token"]), str(value["refresh_token"]), float(value["expires_at"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _snapshot_from_json(raw: str, account_id: str) -> CalendarSnapshot | None:
    try:
        value = json.loads(raw)
        if value.get("account_id") != account_id:
            return None
        events = tuple(CalendarEvent(**item) for item in value["events"])
        return CalendarSnapshot(account_id, float(value["fetched_at"]), events, True)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


class GoogleCalendarProvider:
    def __init__(
        self,
        client_id: str,
        policy: OfflinePolicy | None = None,
        secrets: SecretStore | None = None,
        post: Callable[..., object] = requests.post,
        get: Callable[..., object] = requests.get,
    ) -> None:
        self.client_id = client_id
        self.policy = policy or OfflinePolicy()
        self.secrets = secrets or SecretToolStore()
        self.post = post
        self.get = get

    def save_authorization(self, account: CalendarAccount, token: TokenBundle) -> None:
        if not token.refresh_token:
            raise CalendarError("refresh_token_missing")
        self.secrets.store("google-calendar-token", account.account_id, json.dumps(asdict(token), separators=(",", ":")))

    def disconnect(self, account: CalendarAccount) -> None:
        self.secrets.clear("google-calendar-token", account.account_id)
        self.secrets.clear("google-calendar-cache", account.account_id)

    def sync(self, account: CalendarAccount, *, now: datetime | None = None) -> CalendarSnapshot:
        current = now or datetime.now(timezone.utc)
        try:
            self.policy.require("google_calendar")
            token = self._load_token(account)
            if token.expires_at <= time.time() + 60:
                token = self._refresh_token(account, token)
            events = self._fetch_events(account, token.access_token, current)
            snapshot = CalendarSnapshot(account.account_id, time.time(), events)
            self.secrets.store("google-calendar-cache", account.account_id, json.dumps(asdict(snapshot), separators=(",", ":")))
            return snapshot
        except (OfflineError, CalendarError, requests.RequestException, ValueError, TypeError, KeyError) as exc:
            cached = _snapshot_from_json(self.secrets.lookup("google-calendar-cache", account.account_id), account.account_id)
            if cached:
                return CalendarSnapshot(cached.account_id, cached.fetched_at, cached.events, True, str(exc))
            raise CalendarError(str(exc)) from exc

    def _load_token(self, account: CalendarAccount) -> TokenBundle:
        token = _token_from_json(self.secrets.lookup("google-calendar-token", account.account_id))
        if token is None:
            raise CalendarError("calendar_account_not_connected")
        return token

    def _refresh_token(self, account: CalendarAccount, token: TokenBundle) -> TokenBundle:
        if not self.client_id:
            raise CalendarError("google_oauth_client_id_missing")
        response = self.post(
            TOKEN_URL,
            data={"client_id": self.client_id, "refresh_token": token.refresh_token, "grant_type": "refresh_token"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        refreshed = TokenBundle(
            str(payload["access_token"]),
            token.refresh_token,
            time.time() + int(payload.get("expires_in", 3600)),
        )
        self.save_authorization(account, refreshed)
        return refreshed

    def _fetch_events(self, account: CalendarAccount, access_token: str, now: datetime) -> tuple[CalendarEvent, ...]:
        params = {
            "timeMin": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeMax": (now + timedelta(days=28)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
        }
        response = self.get(
            EVENTS_URL.format(calendar_id=requests.utils.quote(account.calendar_id, safe="")),
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        events: list[CalendarEvent] = []
        for item in payload.get("items", []):
            if not isinstance(item, dict) or item.get("status") == "cancelled":
                continue
            start = item.get("start", {})
            end = item.get("end", {})
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            start_value = start.get("dateTime") or start.get("date")
            end_value = end.get("dateTime") or end.get("date")
            if not start_value or not end_value:
                continue
            events.append(CalendarEvent(
                str(item.get("id", "")), str(item.get("summary", "제목 없음")),
                str(start_value), str(end_value), "dateTime" not in start,
            ))
        return tuple(events)
