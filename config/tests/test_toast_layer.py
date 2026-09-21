from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

from luminophore_shell.ui.notifications import HardwareQuickControlsView, ToastLayer


class HardwareQuickControlsTests(unittest.TestCase):
    def test_master_power_turns_all_off_only_when_every_monitor_is_on(self) -> None:
        view = HardwareQuickControlsView.__new__(HardwareQuickControlsView)
        view._brightness_connectors = ("DP-1", "DP-2")
        view._brightness_powered = {"DP-1": True, "DP-2": True}
        view.set_monitor_power = Mock()

        view._master_power_clicked(Mock())

        view.set_monitor_power.assert_called_once_with(("DP-1", "DP-2"), False)

    def test_master_power_wakes_mixed_monitor_state(self) -> None:
        view = HardwareQuickControlsView.__new__(HardwareQuickControlsView)
        view._brightness_connectors = ("DP-1", "DP-2")
        view._brightness_powered = {"DP-1": False, "DP-2": True}
        view.set_monitor_power = Mock()

        view._master_power_clicked(Mock())

        view.set_monitor_power.assert_called_once_with(("DP-1", "DP-2"), True)

    def test_master_brightness_waits_for_all_monitor_writes(self) -> None:
        self.assertIsNone(
            HardwareQuickControlsView._settled_master_brightness(
                {"DP-1": 50, "DP-2": 100},
                {"DP-2": 50},
            )
        )
        self.assertEqual(
            HardwareQuickControlsView._settled_master_brightness(
                {"DP-1": 50, "DP-2": 50},
                {},
            ),
            50,
        )

    def test_stale_brightness_refresh_is_ignored_after_user_input(self) -> None:
        view = HardwareQuickControlsView.__new__(HardwareQuickControlsView)
        view.brightness_revision = 4
        view._desired_brightness = {}

        view.update_brightness_state(Mock(), expected_revision=3)

    @patch("luminophore_shell.ui.notifications.GLib.idle_add")
    @patch("luminophore_shell.ui.notifications.GLib.source_remove")
    def test_brightness_drag_commits_only_after_release(
        self,
        source_remove: Mock,
        idle_add: Mock,
    ) -> None:
        view = HardwareQuickControlsView.__new__(HardwareQuickControlsView)
        view._brightness_dragging = set()
        view._brightness_timers = {"DP-1": 91}
        view._pending_brightness = {"DP-1": (("DP-1",), 42)}

        view._brightness_drag_started("DP-1")

        source_remove.assert_called_once_with(91)
        self.assertIn("DP-1", view._brightness_dragging)
        idle_add.assert_not_called()

        view._brightness_drag_finished("DP-1")

        self.assertNotIn("DP-1", view._brightness_dragging)
        idle_add.assert_called_once_with(view._commit_brightness, "DP-1")

    def test_brightness_keyboard_commits_on_adjustment_key_release(self) -> None:
        view = HardwareQuickControlsView.__new__(HardwareQuickControlsView)
        view._brightness_drag_started = Mock()
        view._brightness_drag_finished = Mock()

        consumed = view._brightness_key_pressed(Mock(), Gdk.KEY_Down, 0, Gdk.ModifierType(0), "DP-2")
        view._brightness_key_released(Mock(), Gdk.KEY_Down, 0, Gdk.ModifierType(0), "DP-2")

        self.assertFalse(consumed)
        view._brightness_drag_started.assert_called_once_with("DP-2")
        view._brightness_drag_finished.assert_called_once_with("DP-2")

    def test_missing_pointer_event_is_ignored(self) -> None:
        view = HardwareQuickControlsView.__new__(HardwareQuickControlsView)
        view._brightness_drag_started = Mock()
        view._brightness_drag_finished = Mock()

        self.assertFalse(view._brightness_pointer_event(Mock(), None, "DP-1"))
        view._brightness_drag_started.assert_not_called()
        view._brightness_drag_finished.assert_not_called()

    @patch("luminophore_shell.ui.notifications.GLib.timeout_add", return_value=77)
    def test_drag_watchdog_commits_if_release_is_lost(self, timeout_add: Mock) -> None:
        view = HardwareQuickControlsView.__new__(HardwareQuickControlsView)
        view._updating = False
        view._brightness_dragging = {"DP-1"}
        view._brightness_drag_watchdogs = {}
        view._brightness_timers = {}
        view._pending_brightness = {}
        view._desired_brightness = {}
        view.brightness_revision = 0
        scale = Mock()
        scale.get_value.return_value = 42

        view._brightness_changed(scale, "DP-1", ("DP-1",))

        timeout_add.assert_called_once_with(
            view.BRIGHTNESS_DRAG_WATCHDOG_MS,
            view._brightness_drag_timed_out,
            "DP-1",
        )
        self.assertEqual(view._brightness_drag_watchdogs, {"DP-1": 77})

        view.set_brightness = Mock()
        self.assertFalse(view._brightness_drag_timed_out("DP-1"))
        view.set_brightness.assert_called_once_with("DP-1", 42)
        self.assertNotIn("DP-1", view._brightness_dragging)


class ToastLayerLifecycleTests(unittest.TestCase):
    def make_layer(self) -> ToastLayer:
        layer = ToastLayer.__new__(ToastLayer)
        layer.timers = {1: 101, 2: 202}
        layer.deadlines = {1: 1.0, 2: 2.0}
        layer.remaining = {1: 100, 2: 200}
        layer.window = Mock()
        return layer

    @patch("luminophore_shell.ui.notifications.GLib.source_remove")
    def test_stop_cancels_sources_without_destroying_application_window(self, source_remove: Mock) -> None:
        layer = self.make_layer()

        layer.stop()

        self.assertEqual(source_remove.call_count, 2)
        source_remove.assert_any_call(101)
        source_remove.assert_any_call(202)
        self.assertEqual(layer.timers, {})
        self.assertEqual(layer.deadlines, {})
        self.assertEqual(layer.remaining, {})
        layer.window.destroy.assert_not_called()

    @patch("luminophore_shell.ui.notifications.GLib.source_remove")
    def test_destroy_stops_sources_then_destroys_window(self, _source_remove: Mock) -> None:
        layer = self.make_layer()

        layer.destroy()

        layer.window.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
