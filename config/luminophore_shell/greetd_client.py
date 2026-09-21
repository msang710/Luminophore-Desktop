from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
import struct
import threading
from typing import Callable, Mapping


MAX_FRAME_BYTES = 64 * 1024
SESSION_ARGV = (
    "/usr/bin/uwsm",
    "start",
    "-e",
    "-D",
    "Luminophore",
    "luminophore.desktop",
)


class GreetdProtocolError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class AuthResult:
    success: bool
    category: str = ""


SocketFactory = Callable[[str, float], socket.socket]


def _connect_unix(path: str, timeout_seconds: float) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout_seconds)
    try:
        client.connect(path)
    except Exception:
        client.close()
        raise
    return client


def encode_frame(message: Mapping[str, object], max_frame_bytes: int = MAX_FRAME_BYTES) -> bytes:
    payload = json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not payload or len(payload) > max_frame_bytes:
        raise GreetdProtocolError("oversize_frame")
    return struct.pack("<I", len(payload)) + payload


def _recv_exact(client: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = client.recv(remaining)
        if not chunk:
            raise GreetdProtocolError("disconnected")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def decode_frame(client: socket.socket, max_frame_bytes: int = MAX_FRAME_BYTES) -> dict[str, object]:
    length = struct.unpack("<I", _recv_exact(client, 4))[0]
    if length <= 0 or length > max_frame_bytes:
        raise GreetdProtocolError("oversize_frame")
    try:
        message = json.loads(_recv_exact(client, length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GreetdProtocolError("malformed_frame") from exc
    if not isinstance(message, dict):
        raise GreetdProtocolError("invalid_schema")
    _validate_response(message)
    return message


def _validate_response(message: Mapping[str, object]) -> None:
    response_type = message.get("type")
    allowed: dict[str, set[str]] = {
        "success": {"type"},
        "error": {"type", "error_type", "description"},
        "auth_message": {"type", "auth_message_type", "auth_message"},
    }
    if not isinstance(response_type, str) or response_type not in allowed:
        raise GreetdProtocolError("unexpected_message")
    if set(message) - allowed[response_type]:
        raise GreetdProtocolError("invalid_schema")
    if response_type == "error":
        if message.get("error_type") not in {"auth_error", "error"} or not isinstance(message.get("description"), str):
            raise GreetdProtocolError("invalid_schema")
    if response_type == "auth_message":
        if message.get("auth_message_type") not in {"visible", "secret", "info", "error"}:
            raise GreetdProtocolError("invalid_schema")
        if not isinstance(message.get("auth_message"), str):
            raise GreetdProtocolError("invalid_schema")


class GreetdClient:
    """Small fail-closed greetd IPC state machine with no credential logging."""

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        timeout_seconds: float = 5.0,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        socket_factory: SocketFactory = _connect_unix,
    ) -> None:
        self.socket_path = socket_path if socket_path is not None else os.environ.get("GREETD_SOCK", "")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_frame_bytes = max(1024, int(max_frame_bytes))
        self.socket_factory = socket_factory
        self._lock = threading.Lock()
        self._busy = False
        self.state = "idle"

    def _request(self, client: socket.socket, request: Mapping[str, object]) -> dict[str, object]:
        client.sendall(encode_frame(request, self.max_frame_bytes))
        return decode_frame(client, self.max_frame_bytes)

    def _cancel(self, client: socket.socket) -> None:
        try:
            self._request(client, {"type": "cancel_session"})
        except (OSError, GreetdProtocolError, socket.timeout):
            pass

    def authenticate(self, login_user: str, password: str) -> AuthResult:
        with self._lock:
            if self._busy:
                return AuthResult(False, "busy")
            self._busy = True
        try:
            return self._authenticate(login_user, password)
        finally:
            password = ""
            with self._lock:
                self.state = "idle"
                self._busy = False

    def _authenticate(self, login_user: str, password: str) -> AuthResult:
        if not self.socket_path:
            return AuthResult(False, "missing_socket")
        if not login_user or "\x00" in login_user:
            return AuthResult(False, "invalid_user")
        client: socket.socket | None = None
        session_created = False
        try:
            client = self.socket_factory(self.socket_path, self.timeout_seconds)
            client.settimeout(self.timeout_seconds)
            self.state = "creating"
            # Once create_session is sent, cancellation is safe and necessary even if
            # the corresponding reply is malformed or truncated.
            session_created = True
            response = self._request(client, {"type": "create_session", "username": login_user})
            password_used = False
            while True:
                response_type = response["type"]
                if response_type == "success":
                    self.state = "starting"
                    started = self._request(
                        client,
                        {"type": "start_session", "cmd": list(SESSION_ARGV), "env": []},
                    )
                    if started.get("type") != "success":
                        self._cancel(client)
                        return AuthResult(False, "start_failed")
                    self.state = "succeeded"
                    return AuthResult(True)
                if response_type == "error":
                    category = "auth_error" if response.get("error_type") == "auth_error" else "greetd_error"
                    self._cancel(client)
                    return AuthResult(False, category)
                if response_type != "auth_message":
                    raise GreetdProtocolError("unexpected_message")
                self.state = "awaiting_secret"
                message_type = response["auth_message_type"]
                if message_type == "secret" and not password_used:
                    response = self._request(
                        client,
                        {"type": "post_auth_message_response", "response": password},
                    )
                    password = ""
                    password_used = True
                elif message_type in {"info", "error"}:
                    response = self._request(client, {"type": "post_auth_message_response"})
                else:
                    self._cancel(client)
                    return AuthResult(False, "unsupported_prompt")
        except socket.timeout:
            if client is not None and session_created:
                self._cancel(client)
            return AuthResult(False, "timeout")
        except GreetdProtocolError as exc:
            if client is not None and session_created:
                self._cancel(client)
            return AuthResult(False, exc.category)
        except OSError:
            if client is not None and session_created:
                self._cancel(client)
            return AuthResult(False, "disconnected")
        finally:
            if client is not None:
                client.close()
