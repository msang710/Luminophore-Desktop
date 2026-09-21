from __future__ import annotations

from dataclasses import replace
import unittest

from luminophore_shell.hyprland import MonitorRecord, WindowRecord, WorkspaceRef
from luminophore_shell.minimize import MinimizeController


MONITOR = MonitorRecord(0, "DP-2", 0, 0, 1920, 1080, WorkspaceRef(1, "1"))
WINDOW = WindowRecord(
    "0xabc", "ghostty", "com.mitchellh.ghostty", "shell", "shell", 42, 0, "DP-2",
    WorkspaceRef(1, "1"), False, (50, 60), (800, 600), 0, False, True, False,
)


class FakeClient:
    def __init__(self) -> None:
        self.monitor_rows = [MONITOR]
        self.restored: list[object] = []

    def monitors(self):
        return self.monitor_rows

    def windows(self, _monitors):
        return [WINDOW]

    def restore(self, record):
        self.restored.append(record)


class MinimizeControllerTests(unittest.TestCase):
    def test_shell_controller_no_longer_owns_drag_outcomes(self) -> None:
        self.assertFalse(hasattr(MinimizeController, "begin_drag"))
        self.assertFalse(hasattr(MinimizeController, "end_drag"))

    def test_disconnected_original_monitor_waits_without_moving(self) -> None:
        client = FakeClient()
        controller = MinimizeController(client)
        controller.reconcile([replace(WINDOW, placement="minimized")])
        client.monitor_rows = [replace(MONITOR, name="DP-1")]
        result = controller.restore("0xabc")
        self.assertTrue(result.waiting_for_monitor)
        self.assertEqual(client.restored, [])
        self.assertIn("0xabc", controller.records)

    def test_connected_monitor_restores_and_removes_state(self) -> None:
        client = FakeClient()
        controller = MinimizeController(client)
        controller.reconcile([replace(WINDOW, placement="minimized")])
        result = controller.restore("0xabc")
        self.assertTrue(result.restored)
        self.assertEqual(len(client.restored), 1)
        self.assertNotIn("0xabc", controller.records)

    def test_reconcile_projects_compositor_minimized_state(self) -> None:
        client = FakeClient()
        controller = MinimizeController(client)
        minimized = replace(WINDOW, placement="minimized")
        controller.reconcile([minimized])
        self.assertIn("0xabc", controller.records)


if __name__ == "__main__":
    unittest.main()
