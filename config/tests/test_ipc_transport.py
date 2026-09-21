from __future__ import annotations

from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from luminophore_shell.__main__ import _SettingsIpcFacade
from luminophore_shell.ipc import IpcClient, IpcError
from luminophore_shell.settings_contract import (
    SettingsApplyRequest, SettingsResultCategory, SettingsTransportCategory,
)


class FakeSocket:
    def __init__(self, *, connect_error=None, send_error=None, responses=()) -> None:
        self.connect_error = connect_error
        self.send_error = send_error
        self.responses = list(responses)
        self.sent = b""
    def settimeout(self, timeout): self.timeout = timeout
    def connect(self, path):
        if self.connect_error: raise self.connect_error
    def sendall(self, payload):
        self.sent += payload
        if self.send_error: raise self.send_error
    def recv(self, size):
        if not self.responses: return b""
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value
    def close(self): return None


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def request(self, payload):
        self.requests.append(dict(payload))
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class OfflineWriter:
    writes = 0
    def __init__(self, path): self.path = path
    def validate(self, changes): return None
    def write_atomic(self, changes, expected_digest):
        type(self).writes += 1
        return "offline-digest-2"
    def digest(self): return "offline-digest-1"


class IpcTransportTests(unittest.TestCase):
    def _request_with(self, fake: FakeSocket):
        with patch("luminophore_shell.ipc.socket.socket", return_value=fake):
            return IpcClient(Path("/tmp/unused-control.sock")).request({"command": "settings-apply"})

    def test_server_unknown_is_same_as_lost_transport_ack(self):
        with self.assertRaises(IpcError) as raised:
            self._request_with(FakeSocket(responses=(b'{"ok":false,"category":"completion_unknown"}\n',)))
        self.assertEqual(raised.exception.category, SettingsTransportCategory.COMPLETION_UNKNOWN)

    def test_connection_refused_is_connection_unavailable(self) -> None:
        with self.assertRaises(IpcError) as raised:
            self._request_with(FakeSocket(connect_error=ConnectionRefusedError()))
        self.assertEqual(raised.exception.category, SettingsTransportCategory.CONNECTION_UNAVAILABLE)

    def test_connection_unavailable_never_writes_an_offline_file(self) -> None:
        OfflineWriter.writes = 0
        client = ScriptedClient((IpcError('refused', SettingsTransportCategory.CONNECTION_UNAVAILABLE),))
        with self.assertRaises(IpcError):
            _SettingsIpcFacade(client).apply(SettingsApplyRequest.build('offline-1', 'old', {'theme.mode':'dark'}))
        self.assertEqual(OfflineWriter.writes, 0)

    def test_send_error_timeout_and_ack_loss_are_completion_unknown(self) -> None:
        cases = (
            FakeSocket(send_error=BrokenPipeError()),
            FakeSocket(responses=(socket.timeout(),)),
            FakeSocket(responses=(b"",)),
        )
        for fake in cases:
            with self.subTest(fake=fake), self.assertRaises(IpcError) as raised:
                self._request_with(fake)
            self.assertEqual(raised.exception.category, SettingsTransportCategory.COMPLETION_UNKNOWN)

    def test_malformed_response_is_protocol_error_without_echo(self) -> None:
        with self.assertRaises(IpcError) as raised:
            self._request_with(FakeSocket(responses=(b'/private/wallpaper.png\n',)))
        self.assertEqual(raised.exception.category, SettingsTransportCategory.PROTOCOL_ERROR)
        self.assertNotIn("/private", str(raised.exception))

    def test_remote_rejection_is_not_offline_fallback(self) -> None:
        facade = _SettingsIpcFacade(ScriptedClient(({
            "ok": False, "error": "rejected /private/wallpaper.png",
        },)))
        request = SettingsApplyRequest.build("r-reject", "digest-1", {"theme.mode": "dark"})
        with self.assertRaises(IpcError) as raised:
            facade.apply(request)
        self.assertEqual(raised.exception.category, SettingsTransportCategory.REMOTE_REJECTION)
        self.assertNotIn("/private", str(raised.exception))

    def test_malformed_success_payload_is_protocol_error_not_fallback(self) -> None:
        facade = _SettingsIpcFacade(ScriptedClient(({"ok": True, "request_id": "r-bad"},)))
        request = SettingsApplyRequest.build("r-bad", "digest-1", {"theme.mode": "dark"})
        with self.assertRaises(IpcError) as raised:
            facade.apply(request)
        self.assertEqual(raised.exception.category, SettingsTransportCategory.PROTOCOL_ERROR)

    def test_ack_loss_blocks_resend_until_status_returns_exact_result(self) -> None:
        unknown = IpcError("ack lost", SettingsTransportCategory.COMPLETION_UNKNOWN)
        client = ScriptedClient((unknown, {
            "ok": True, "found": True, "version": 1, "request_id": "r-1",
            "category": "ok", "phase": "complete", "digest": "digest-2",
            "changed_paths": ["theme.mode"], "message": "applied",
        }))
        facade = _SettingsIpcFacade(client)
        request = SettingsApplyRequest.build("r-1", "digest-1", {"theme.mode": "dark"})
        first = facade.apply(request)
        second = facade.apply(request)
        self.assertEqual(first.category, SettingsResultCategory.COMPLETION_UNKNOWN)
        self.assertEqual(second.category, SettingsResultCategory.OK)
        self.assertEqual([row["command"] for row in client.requests], ["settings-apply", "settings-status"])

    def test_missing_status_keeps_unknown_and_never_resends_or_falls_back(self) -> None:
        unknown = IpcError("timeout", SettingsTransportCategory.COMPLETION_UNKNOWN)
        client = ScriptedClient((unknown, {"ok": True, "found": False}))
        facade = _SettingsIpcFacade(client)
        request = SettingsApplyRequest.build("r-2", "digest-1", {"appearance.wallpaper_path": "/private/a.png"})
        facade.apply(request)
        result = facade.apply(request)
        self.assertEqual(result.category, SettingsResultCategory.COMPLETION_UNKNOWN)
        self.assertEqual([row["command"] for row in client.requests], ["settings-apply", "settings-status"])
        self.assertNotIn("/private/a.png", result.message)


if __name__ == "__main__":
    unittest.main()
