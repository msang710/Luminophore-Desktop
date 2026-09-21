from __future__ import annotations

import unittest
from unittest.mock import patch

from luminophore_shell.clipboard import ClipboardHistory, ClipboardItem


class FakeSecrets:
    def __init__(self) -> None:
        self.value = ""
        self.cleared = False

    def lookup(self) -> str:
        return self.value

    def store(self, value: str) -> None:
        self.value = value

    def clear(self) -> None:
        self.value = ""
        self.cleared = True


class ClipboardHistoryTests(unittest.TestCase):
    def test_text_and_image_round_trip_through_secret_service_boundary(self) -> None:
        secrets = FakeSecrets()
        history = ClipboardHistory(5, retention_hours=24, secrets=secrets, now=lambda: 10_000)
        history._insert(ClipboardItem("text/plain;charset=utf-8", b"private clipboard text", 10_000))
        history._insert(ClipboardItem("image/png", b"\x89PNG\r\nprivate pixels", 10_000))

        self.assertNotIn("private clipboard text", secrets.value)
        self.assertNotIn("private pixels", secrets.value)
        restored = ClipboardHistory(5, retention_hours=24, secrets=secrets, now=lambda: 10_000)
        restored._restore()

        self.assertEqual([(item.mime_type, item.data) for item in restored.items], [
            ("image/png", b"\x89PNG\r\nprivate pixels"),
            ("text/plain;charset=utf-8", b"private clipboard text"),
        ])

    def test_retention_and_limit_are_applied_on_restore(self) -> None:
        secrets = FakeSecrets()
        source = ClipboardHistory(3, retention_hours=1, secrets=secrets, now=lambda: 10_000)
        source.items = [
            ClipboardItem("text/plain", b"new", 9_999),
            ClipboardItem("text/plain", b"old", 6_000),
        ]
        source._persist()
        restored = ClipboardHistory(1, retention_hours=1, secrets=secrets, now=lambda: 10_000)
        restored._restore()
        self.assertEqual([item.text for item in restored.items], ["new"])

    @patch("luminophore_shell.clipboard.subprocess.run")
    def test_clear_removes_selection_and_secret_history(self, run) -> None:
        secrets = FakeSecrets()
        history = ClipboardHistory(5, secrets=secrets)
        history.items = [ClipboardItem("text/plain", b"value", 1)]
        history.clear()
        self.assertEqual(history.items, [])
        self.assertTrue(secrets.cleared)
        run.assert_called_once_with(["wl-copy", "--clear"], check=False, timeout=0.5)


if __name__ == "__main__":
    unittest.main()
