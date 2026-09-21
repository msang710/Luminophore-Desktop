from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from luminophore_shell.app import LuminophoreShellApplication
from luminophore_shell.screen_color_picker import ScreenColorPickResult


class FakeWindow:
    def __init__(self) -> None:
        self.visible: list[bool] = []
        self.presented = 0

    def set_visible(self, visible: bool) -> None:
        self.visible.append(visible)

    def present(self) -> None:
        self.presented += 1


class FakeSurface:
    def __init__(self) -> None:
        self.expanded = True
        self.window = FakeWindow()
        self._closed = None

    def when_transition_complete(self, expanded: bool, callback) -> None:
        if not expanded:
            self._closed = callback

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        if not expanded and self._closed:
            callback = self._closed
            self._closed = None
            callback()

    def contains_global_point(self, _x: int, _y: int) -> bool:
        return False


class FakePicker:
    available = True

    def __init__(self) -> None:
        self.busy = False
        self.completed = None

    def pick(self, completed) -> bool:
        self.busy = True
        self.completed = completed
        return True

    def finish(self, result: ScreenColorPickResult) -> None:
        self.busy = False
        self.completed(result)


class FakeSystemView:
    def __init__(self) -> None:
        self.resumed = 0

    def resume_settings_after_screen_pick(self) -> None:
        self.resumed += 1


class PickerFlowHarness:
    _screen_color_pick_busy = LuminophoreShellApplication._screen_color_pick_busy
    _begin_screen_color_pick = LuminophoreShellApplication._begin_screen_color_pick
    _launch_screen_color_picker = LuminophoreShellApplication._launch_screen_color_picker
    _finish_screen_color_pick = LuminophoreShellApplication._finish_screen_color_pick
    _dismiss_expanded_at = LuminophoreShellApplication._dismiss_expanded_at

    def __init__(self) -> None:
        self.screen_color_picker = FakePicker()
        self._screen_pick_pending = False
        self._last_screen_color_pick = None
        self._outside_click_suppressed_until = 0.0
        self.surface = FakeSurface()
        self.surfaces = {"system": self.surface}
        self.system_view = FakeSystemView()


class ScreenColorPickFlowTests(unittest.TestCase):
    def test_surface_hides_before_pick_and_same_settings_view_resumes(self) -> None:
        app = PickerFlowHarness()
        results: list[tuple[ScreenColorPickResult, int]] = []

        with (
            patch("luminophore_shell.app.GLib.timeout_add", side_effect=lambda _ms, callback, *args: callback(*args)),
            patch("luminophore_shell.app.GLib.idle_add", side_effect=lambda callback, *args: callback(*args)),
        ):
            self.assertTrue(app._begin_screen_color_pick(
                lambda result: results.append((result, app.system_view.resumed))
            ))
            self.assertEqual(app.surface.window.visible, [False])
            self.assertFalse(app.surface.expanded)
            self.assertTrue(app._screen_pick_pending)

            expected = ScreenColorPickResult(True, "#123456", "선택 완료")
            app.screen_color_picker.finish(expected)

        self.assertEqual(results, [(expected, 0)])
        self.assertEqual(app._last_screen_color_pick, expected)
        self.assertFalse(app._screen_pick_pending)
        self.assertEqual(app.system_view.resumed, 1)
        self.assertEqual(app.surface.window.presented, 1)
        self.assertTrue(app.surface.expanded)
        self.assertGreater(app._outside_click_suppressed_until, time.monotonic())

    def test_completion_click_guard_does_not_close_restored_surface(self) -> None:
        app = PickerFlowHarness()
        app._outside_click_suppressed_until = time.monotonic() + 1.0

        app._dismiss_expanded_at(10, 10)

        self.assertTrue(app.surface.expanded)


if __name__ == "__main__":
    unittest.main()
