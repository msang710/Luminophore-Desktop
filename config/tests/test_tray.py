from __future__ import annotations

import unittest

from luminophore_shell.tray import parse_menu_layout


class TrayMenuTests(unittest.TestCase):
    def test_parses_visible_actions_separator_and_nested_menu(self) -> None:
        layout = (
            0,
            {"children-display": "submenu"},
            [
                (1, {"label": "_Show", "enabled": True}, []),
                (2, {"type": "separator"}, []),
                (3, {"label": "Hidden", "visible": False}, []),
                (4, {"label": "More"}, [(5, {"label": "E_xit", "toggle-state": 1}, [])]),
            ],
        )

        entries = parse_menu_layout(layout)

        self.assertEqual(entries[0].label, "Show")
        self.assertTrue(entries[1].separator)
        self.assertFalse(entries[2].visible)
        self.assertEqual(entries[3].children[0].label, "Exit")
        self.assertEqual(entries[3].children[0].toggle_state, 1)

    def test_malformed_children_fail_closed(self) -> None:
        self.assertEqual(parse_menu_layout("bad"), ())
        self.assertEqual(parse_menu_layout((0, {}, ["bad"])), ())


if __name__ == "__main__":
    unittest.main()
