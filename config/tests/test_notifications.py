from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.config import NotificationConfig
from luminophore_shell.notifications import NotificationManager, notification_target_connector


class NotificationManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_patch = patch("luminophore_shell.notifications.state_dir", return_value=Path(self.temp.name))
        self.state_patch.start()
        self.popups = []
        self.manager = NotificationManager(NotificationConfig(), lambda: None, self.popups.append)

    def tearDown(self) -> None:
        self.state_patch.stop()
        self.temp.cleanup()

    def test_dnd_notification_goes_directly_to_history(self) -> None:
        self.manager.set_dnd(True)
        notification_id = self.manager.notify("chat", 0, "", "hello", "body", [], {}, -1)
        self.assertEqual(self.popups, [])
        self.assertEqual(self.manager.history[0].id, notification_id)

    def test_hardware_danger_obeys_dnd(self) -> None:
        self.manager.set_dnd(True)
        notification_id = self.manager.notify_hardware("danger", "hot")
        self.assertEqual(self.popups, [])
        self.assertEqual(self.manager.history[0].id, notification_id)

    def test_timeout_enters_history_but_dismiss_does_not(self) -> None:
        notification_id = self.manager.notify("app", 0, "", "one", "", [], {}, 10)
        self.manager.popup_expired(notification_id)
        self.assertEqual([item.id for item in self.manager.history], [notification_id])
        self.manager.dismiss(notification_id)
        self.assertEqual(self.manager.history, [])

    def test_replacement_reuses_id(self) -> None:
        first = self.manager.notify("app", 0, "", "one", "", [], {}, -1)
        second = self.manager.notify("app", first, "", "two", "", [], {}, -1)
        self.assertEqual(first, second)
        self.assertEqual(self.manager.notifications[first].summary, "two")

    def test_expired_history_is_removed_by_retention(self) -> None:
        self.manager.set_dnd(True)
        notification_id = self.manager.notify("app", 0, "", "old", "", [], {}, -1)
        self.manager.history[0].created_at = 100
        changed = self.manager.prune_history(now=100 + self.manager.config.retention_hours * 3600 + 1)
        self.assertTrue(changed)
        self.assertNotIn(notification_id, self.manager.notifications)
        self.assertEqual(self.manager.history, [])

    def test_monitor_routing_prefers_focus_and_has_deterministic_fallback(self) -> None:
        available = ("DP-1", "DP-2")
        self.assertEqual(notification_target_connector("DP-2", available), "DP-2")
        self.assertEqual(notification_target_connector("missing", available), "DP-1")
        self.assertEqual(notification_target_connector("DP-1", ()), "")


if __name__ == "__main__":
    unittest.main()
