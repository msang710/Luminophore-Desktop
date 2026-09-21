from __future__ import annotations

import unittest

from luminophore_shell.settings_backend import SettingsBackend
from luminophore_shell.settings_contract import SETTINGS_PROTOCOL_VERSION, SettingsApplyRequest, decode_message, encode_message


class Coordinator:
    def __init__(self) -> None:
        self.results = {}
        self.last_request = None
    def apply(self, request):
        from luminophore_shell.settings_contract import SettingsApplyPhase, SettingsApplyResult, SettingsResultCategory
        self.last_request = request
        result = SettingsApplyResult(
            request.request_id, SettingsResultCategory.OK, SettingsApplyPhase.COMPLETE,
            "digest-2", tuple(key for key, _value in request.changes), "applied",
        )
        self.results[request.request_id] = result
        return result
    def status(self, request_id): return self.results.get(request_id)


class SettingsBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = Coordinator()
        self.backend = SettingsBackend(self.coordinator)

    def test_apply_is_protocol_versioned_and_canonical(self) -> None:
        response = decode_message(self.backend.handle(encode_message({
            "version": SETTINGS_PROTOCOL_VERSION, "command": "apply", "request_id": "r-1",
            "expected_digest": "digest-1", "changes": {"theme.mode": "light"},
        })))
        self.assertEqual(response["category"], "ok")
        self.assertEqual(self.coordinator.last_request, SettingsApplyRequest.build("r-1", "digest-1", {"theme.mode": "light"}))

    def test_status_never_leaks_values_or_source_paths(self) -> None:
        private_path = "/home/user/Pictures/private-wallpaper.png"
        self.backend.handle(encode_message({
            "version": 1, "command": "apply", "request_id": "r-1", "expected_digest": "digest-1",
            "changes": {"appearance.wallpaper_path": private_path},
        }))
        raw = self.backend.handle(encode_message({"version": 1, "command": "status", "request_id": "r-1"}))
        response = decode_message(raw)
        self.assertNotIn(private_path.encode(), raw)
        self.assertNotIn("values", response)
        self.assertNotIn("changes", response)
        self.assertEqual(response["changed_paths"], ["appearance.wallpaper_path"])

    def test_unknown_status_is_minimal_and_unknown_fields_fail_closed(self) -> None:
        response = decode_message(self.backend.handle(encode_message({
            "version": 1, "command": "status", "request_id": "missing",
        })))
        self.assertEqual(response, {"version": 1, "request_id": "missing", "found": False})
        with self.assertRaisesRegex(ValueError, "fields"):
            self.backend.handle(encode_message({
                "version": 1, "command": "status", "request_id": "missing", "private": "secret",
            }))

    def test_unknown_version_and_command_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.backend.handle(b'{"version":99,"command":"status","request_id":"r"}')
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.backend.handle(encode_message({"version": 1, "command": "dump"}))


if __name__ == "__main__":
    unittest.main()
