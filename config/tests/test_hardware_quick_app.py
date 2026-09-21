from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

from luminophore_shell.app import LuminophoreShellApplication
from luminophore_shell.hardware_controls import DdcTarget


class BrightnessKeyHarness:
    _preview_keyboard_brightness = LuminophoreShellApplication._preview_keyboard_brightness
    _commit_keyboard_brightness = LuminophoreShellApplication._commit_keyboard_brightness
    _wake_all_monitors_if_dark = LuminophoreShellApplication._wake_all_monitors_if_dark

    def __init__(self) -> None:
        self._brightness_key_lock = threading.Lock()
        self._brightness_key_intents: dict[str, int] = {}
        self._brightness_values = {"DP-2": 50}
        self._monitor_powered = {"DP-2": True}
        self._ddc_targets = {"DP-2": DdcTarget("DP-2", 5)}
        self.brightness_queue = Mock()
        self._hardware_brightness_previewed = Mock()
        self._hardware_brightness_applied = Mock()
        self._quick_brightness_failed = Mock()
        self._quick_brightness_rejected = Mock()
        self._refresh_hardware_quick_controls = Mock()
        self._set_monitor_power = Mock()


class HardwareQuickAppTests(unittest.TestCase):
    @patch("luminophore_shell.app.GLib.idle_add")
    def test_repeated_keyboard_preview_writes_ddc_only_once_on_commit(self, idle_add: Mock) -> None:
        app = BrightnessKeyHarness()

        first = app._preview_keyboard_brightness("DP-2", 5)
        second = app._preview_keyboard_brightness("DP-2", 5)

        self.assertEqual(first["value"], 55)
        self.assertEqual(second["value"], 60)
        app.brightness_queue.submit_percent.assert_not_called()
        self.assertEqual(app._brightness_key_intents, {"DP-2": 60})

        result = app._commit_keyboard_brightness()

        self.assertTrue(result["accepted"])
        app.brightness_queue.submit_percent.assert_called_once()
        args = app.brightness_queue.submit_percent.call_args.args
        self.assertEqual((args[0], args[1]), (DdcTarget("DP-2", 5), 60))
        self.assertEqual(app._brightness_key_intents, {})
        self.assertEqual(idle_add.call_count, 2)

    @patch("luminophore_shell.app.GLib.idle_add")
    def test_brightness_key_wakes_all_dark_monitors_before_preview(self, _idle_add: Mock) -> None:
        app = BrightnessKeyHarness()
        app._ddc_targets["DP-1"] = DdcTarget("DP-1", 6)
        app._brightness_values["DP-1"] = 40
        app._monitor_powered = {"DP-1": False, "DP-2": False}

        result = app._preview_keyboard_brightness("DP-2", 5)

        self.assertTrue(result["accepted"])
        app._set_monitor_power.assert_called_once_with(("DP-1", "DP-2"), True)


if __name__ == "__main__":
    unittest.main()
