from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from luminophore_shell.screen_color_picker import (
    ScreenColorPickResult,
    ScreenColorPicker,
    hyprpicker_command,
    parse_picked_color,
)


class FakeProcess:
    def __init__(
        self,
        stdout: str,
        stderr: str = "",
        returncode: int = 0,
        gate: threading.Event | None = None,
        entered: threading.Event | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.gate = gate
        self.entered = entered
        self.terminated = False
        self.killed = False

    def communicate(self) -> tuple[str, str]:
        if self.entered:
            self.entered.set()
        if self.gate:
            self.gate.wait(1.0)
        return self.stdout, self.stderr

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class ScreenColorPickerTests(unittest.TestCase):
    def test_parser_normalizes_plain_or_decorated_hex(self) -> None:
        self.assertEqual(parse_picked_color("#1aB2c3\n"), "#1AB2C3")
        self.assertEqual(parse_picked_color("selected: #abcdef"), "#ABCDEF")
        self.assertEqual(parse_picked_color("112233\n"), "#112233")
        self.assertIsNone(parse_picked_color("#12345"))
        self.assertIsNone(parse_picked_color("selected: 112233"))

    def test_command_does_not_touch_clipboard_or_notifications(self) -> None:
        command = hyprpicker_command("/usr/bin/hyprpicker")

        self.assertEqual(
            command,
            ["/usr/bin/hyprpicker", "--format=hex", "--no-fancy", "--render-inactive"],
        )
        self.assertNotIn("--autocopy", command)
        self.assertNotIn("--notify", command)
        self.assertNotIn("--quiet", command)

    def test_success_is_reported_from_worker(self) -> None:
        process = FakeProcess("#12ab34\n")
        completed = threading.Event()
        results: list[ScreenColorPickResult] = []
        picker = ScreenColorPicker("/usr/bin/hyprpicker")

        with patch("luminophore_shell.screen_color_picker.subprocess.Popen", return_value=process) as popen:
            self.assertTrue(picker.pick(lambda result: (results.append(result), completed.set())))
            self.assertTrue(completed.wait(1.0))

        self.assertEqual(results, [ScreenColorPickResult(True, "#12AB34", "화면에서 색상을 가져왔습니다")])
        self.assertFalse(picker.busy)
        popen.assert_called_once_with(
            ["/usr/bin/hyprpicker", "--format=hex", "--no-fancy", "--render-inactive"],
            stdout=-1,
            stderr=-1,
            text=True,
            start_new_session=True,
        )
        picker.shutdown()

    def test_duplicate_pick_is_rejected_while_first_is_waiting(self) -> None:
        gate = threading.Event()
        entered = threading.Event()
        completed = threading.Event()
        process = FakeProcess("#123456\n", gate=gate, entered=entered)
        picker = ScreenColorPicker("/usr/bin/hyprpicker")

        with patch("luminophore_shell.screen_color_picker.subprocess.Popen", return_value=process):
            self.assertTrue(picker.pick(lambda _result: completed.set()))
            self.assertTrue(entered.wait(1.0))
            self.assertFalse(picker.pick(lambda _result: None))
            gate.set()
            self.assertTrue(completed.wait(1.0))

        picker.shutdown()

    def test_cancel_preserves_an_error_result_without_a_color(self) -> None:
        process = FakeProcess("", returncode=1)
        completed = threading.Event()
        results: list[ScreenColorPickResult] = []
        picker = ScreenColorPicker("/usr/bin/hyprpicker")

        with patch("luminophore_shell.screen_color_picker.subprocess.Popen", return_value=process):
            self.assertTrue(picker.pick(lambda result: (results.append(result), completed.set())))
            self.assertTrue(completed.wait(1.0))

        self.assertEqual(results[0].error_category, "cancelled")
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].color, "")
        picker.shutdown()

    def test_missing_picker_is_unavailable_and_starts_nothing(self) -> None:
        picker = ScreenColorPicker("")

        with patch("luminophore_shell.screen_color_picker.subprocess.Popen") as popen:
            self.assertFalse(picker.available)
            self.assertFalse(picker.pick(lambda _result: None))

        popen.assert_not_called()
        picker.shutdown()


if __name__ == "__main__":
    unittest.main()
