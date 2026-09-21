from __future__ import annotations

import unittest

from luminophore_shell.hyprland import WindowRecord, WorkspaceRef
from luminophore_shell.window_glow_demo import DEMO_CLASS, DEMO_TITLE, WindowGlowProfile, demo_window


def _window(pid: int, app_class: str = "app", title: str = "title") -> WindowRecord:
    return WindowRecord(
        "0x1", app_class, app_class, title, title, pid, 0, "DP-1",
        WorkspaceRef(1, "1"), False, (100, 80), (900, 600), 0, False, True, False,
    )


class WindowGlowDemoTests(unittest.TestCase):
    def test_demo_window_is_bounded_to_spawned_terminal(self) -> None:
        unrelated = _window(10)
        spawned = _window(20)

        self.assertIs(demo_window([unrelated, spawned], 20), spawned)

    def test_demo_window_accepts_stable_demo_class_after_terminal_forks(self) -> None:
        demo = _window(99, DEMO_CLASS)

        self.assertIs(demo_window([demo], 20), demo)

    def test_window_profile_is_distinct_static_low_energy_bloom(self) -> None:
        frame = WindowGlowProfile().frame(900, 600)

        self.assertEqual((frame.width, frame.height), (900.0, 600.0))
        self.assertEqual(frame.visible_extent, 42.0)
        self.assertEqual(frame.outline_width, 2.0)
        self.assertEqual(frame.phase, 0.0)
        self.assertLess(frame.core_energy, 0.16)

    def test_demo_title_is_stable_for_the_dedicated_window_rule(self) -> None:
        self.assertEqual(DEMO_TITLE, "LUMINOPHORE Window Glow Demo")


if __name__ == "__main__":
    unittest.main()
