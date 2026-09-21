from __future__ import annotations

import unittest

from luminophore_shell.ui.system import SystemView


class FakeStack:
    def __init__(self) -> None:
        self.visible = ""

    def set_visible_child_name(self, name: str) -> None:
        self.visible = name


class SystemViewModeTests(unittest.TestCase):
    def test_every_settings_page_requests_full_interactive_mode(self) -> None:
        modes: list[bool] = []
        view = SystemView.__new__(SystemView)
        view.on_settings_mode = modes.append
        view.settings_mode = False
        view.expanded = FakeStack()

        for page in ("settings", "system-theme"):
            view._show_page(page, True)
            self.assertEqual(view.expanded.visible, page)
            self.assertTrue(modes[-1])
            self.assertTrue(view.settings_mode)

        view._show_page("metrics", False)
        self.assertEqual(view.expanded.visible, "metrics")
        self.assertFalse(modes[-1])
        self.assertFalse(view.settings_mode)


if __name__ == "__main__":
    unittest.main()
