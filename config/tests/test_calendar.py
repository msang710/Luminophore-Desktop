from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import urlopen

from luminophore_shell.calendar import (
    CalendarAccount,
    CalendarError,
    GoogleCalendarProvider,
    GoogleOAuthFlow,
    OAuthClient,
    SecretToolStore,
    TokenBundle,
    authorization_url,
    calendar_error_message,
    install_oauth_client,
    load_oauth_client,
    pkce_pair,
)
from luminophore_shell.network_policy import OfflinePolicy


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def lookup(self, kind: str, account_id: str) -> str:
        return self.values.get((kind, account_id), "")

    def store(self, kind: str, account_id: str, value: str) -> None:
        self.values[(kind, account_id)] = value

    def clear(self, kind: str, account_id: str) -> None:
        self.values.pop((kind, account_id), None)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class CalendarProviderTests(unittest.TestCase):
    def test_oauth_client_file_requires_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "client.json"
            path.write_text(json.dumps({"installed": {
                "client_id": "client", "client_secret": "secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }}), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(CalendarError, "0600"):
                load_oauth_client(path)
            path.chmod(0o600)
            self.assertEqual(load_oauth_client(path).client_id, "client")

    def test_downloaded_oauth_client_is_validated_and_installed_privately(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "download.json"
            destination = root / "config/luminophore-shell/google-oauth-client.json"
            source.write_text(json.dumps({"installed": {
                "client_id": "client", "client_secret": "secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }}), encoding="utf-8")
            source.chmod(0o644)
            client = install_oauth_client(source, destination)
            self.assertEqual(client.client_id, "client")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o700)

    def test_calendar_errors_have_visible_user_messages(self) -> None:
        self.assertIn("OAuth JSON", calendar_error_message("google_oauth_client_missing"))
        self.assertIn("연결되지", calendar_error_message("calendar_account_not_connected"))

    def test_authorization_url_uses_pkce_state_and_offline_consent(self) -> None:
        verifier, challenge = pkce_pair()
        query = parse_qs(urlparse(authorization_url(
            OAuthClient("client", "secret"), "http://127.0.0.1:1234/oauth2callback", "state", challenge,
        )).query)
        self.assertGreater(len(verifier), 43)
        self.assertEqual(query["state"], ["state"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])

    def test_loopback_flow_validates_callback_and_exchanges_code(self) -> None:
        posts: list[dict[str, object]] = []

        def open_browser(url: str) -> bool:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            redirect = urlparse(query["redirect_uri"][0])
            callback = urlunparse(redirect._replace(query=urlencode({"state": query["state"][0], "code": "auth-code"})))
            threading.Thread(target=lambda: urlopen(callback, timeout=2).read(), daemon=True).start()
            return True

        def post(_url: str, **kwargs: object) -> FakeResponse:
            posts.append(kwargs)
            return FakeResponse({"access_token": "access", "refresh_token": "refresh", "expires_in": 3600})

        try:
            token = GoogleOAuthFlow(OAuthClient("client", "secret"), post=post, open_browser=open_browser).run(2)
        except PermissionError:
            self.skipTest("NOT_RUN(reason=PHYSICAL_ENVIRONMENT_UNAVAILABLE): loopback sockets are blocked")
        self.assertEqual(token.refresh_token, "refresh")
        self.assertEqual(posts[0]["data"]["code"], "auth-code")
        self.assertIn("code_verifier", posts[0]["data"])

    def test_secret_tool_never_places_secret_in_argv(self) -> None:
        calls: list[tuple[tuple[str, ...], object]] = []

        def runner(argv, **kwargs):
            calls.append((tuple(argv), kwargs.get("input")))
            return subprocess.CompletedProcess(argv, 0, "", "")

        SecretToolStore(runner).store("google-calendar-token", "account-1", "refresh-secret")
        self.assertNotIn("refresh-secret", calls[0][0])
        self.assertEqual(calls[0][1], "refresh-secret")

    def test_sync_reads_events_and_stores_cache_in_secret_service(self) -> None:
        secrets = FakeSecrets()
        account = CalendarAccount("account-1", "user@example.com")
        token = TokenBundle("access-secret", "refresh-secret", time.time() + 3600)
        secrets.store("google-calendar-token", account.account_id, json.dumps(asdict(token)))
        calls: list[dict[str, object]] = []

        def get(_url, **kwargs):
            calls.append(kwargs)
            return FakeResponse({"items": [{
                "id": "event-1", "summary": "회의", "status": "confirmed",
                "start": {"dateTime": "2026-08-24T10:00:00+09:00"},
                "end": {"dateTime": "2026-08-24T11:00:00+09:00"},
            }, {"id": "removed", "status": "cancelled"}]})

        snapshot = GoogleCalendarProvider("client", secrets=secrets, get=get).sync(
            account, now=datetime(2026, 8, 24, tzinfo=timezone.utc)
        )
        self.assertEqual(snapshot.events[0].summary, "회의")
        self.assertFalse(snapshot.stale)
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer access-secret")
        self.assertIn(("google-calendar-cache", account.account_id), secrets.values)

    def test_offline_uses_account_scoped_last_good_without_transport(self) -> None:
        secrets = FakeSecrets()
        account = CalendarAccount("account-1", "user@example.com")
        secrets.store("google-calendar-cache", account.account_id, json.dumps({
            "account_id": account.account_id,
            "fetched_at": 1,
            "events": [{"event_id": "one", "summary": "cached", "start": "2026-08-24", "end": "2026-08-25", "all_day": True}],
            "stale": False,
            "error": "",
        }))
        calls: list[object] = []
        provider = GoogleCalendarProvider(
            "client", policy=OfflinePolicy(True), secrets=secrets,
            get=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        snapshot = provider.sync(account)
        self.assertTrue(snapshot.stale)
        self.assertEqual(snapshot.events[0].summary, "cached")
        self.assertEqual(calls, [])

    def test_corrupt_or_other_account_cache_is_not_used(self) -> None:
        secrets = FakeSecrets()
        account = CalendarAccount("account-1", "user@example.com")
        secrets.store("google-calendar-cache", account.account_id, '{"account_id":"account-2"}')
        provider = GoogleCalendarProvider("client", policy=OfflinePolicy(True), secrets=secrets)
        with self.assertRaises(CalendarError):
            provider.sync(account)

    def test_expired_token_refresh_preserves_refresh_token(self) -> None:
        secrets = FakeSecrets()
        account = CalendarAccount("account-1", "user@example.com")
        secrets.store("google-calendar-token", account.account_id, json.dumps(asdict(TokenBundle("old", "refresh", 0))))
        posts: list[dict[str, object]] = []

        def post(_url, **kwargs):
            posts.append(kwargs)
            return FakeResponse({"access_token": "new", "expires_in": 3600})

        provider = GoogleCalendarProvider(
            "client-id", secrets=secrets, post=post,
            get=lambda *_args, **_kwargs: FakeResponse({"items": []}),
        )
        provider.sync(account)
        saved = json.loads(secrets.lookup("google-calendar-token", account.account_id))
        self.assertEqual(saved["refresh_token"], "refresh")
        self.assertEqual(posts[0]["data"]["grant_type"], "refresh_token")


if __name__ == "__main__":
    unittest.main()
