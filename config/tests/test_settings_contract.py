import unittest

from luminophore_shell.settings_contract import (
    MAX_SETTINGS_MESSAGE_BYTES,
    SETTINGS_PROTOCOL_VERSION,
    SettingsApplyPhase,
    SettingsApplyRequest,
    SettingsApplyResult,
    SettingsResultCategory,
    decode_message,
    encode_message,
)


class SettingsContractTests(unittest.TestCase):
    def test_message_round_trip_is_deterministic(self) -> None:
        payload = {"version": SETTINGS_PROTOCOL_VERSION, "command": "status", "request_id": "abc"}
        encoded = encode_message(payload)
        self.assertEqual(encoded, encode_message(dict(reversed(tuple(payload.items())))))
        self.assertEqual(decode_message(encoded), payload)

    def test_unknown_protocol_version_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            decode_message(b'{"version":99}')

    def test_message_limit_is_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "size limit"):
            decode_message(b"x" * (MAX_SETTINGS_MESSAGE_BYTES + 1))

    def test_apply_request_is_canonical(self) -> None:
        request = SettingsApplyRequest.build("request-1", "digest", {"theme.mode": "dark", "compositor.blur_size": 2})
        self.assertEqual(request.changes[0][0], "compositor.blur_size")
        with self.assertRaises(ValueError):
            SettingsApplyRequest("bad id", "digest", (("a", 1),))

    def test_status_wire_does_not_contain_values_or_wallpaper_paths(self) -> None:
        result = SettingsApplyResult(
            "request-1",
            SettingsResultCategory.OK,
            SettingsApplyPhase.COMPLETE,
            "digest",
            ("appearance.wallpaper_provider",),
            "적용됨",
        )
        wire = result.status_wire()
        self.assertNotIn("values", wire)
        self.assertNotIn("source", wire)
        self.assertEqual(wire["changed_paths"], ["appearance.wallpaper_provider"])


if __name__ == "__main__":
    unittest.main()
