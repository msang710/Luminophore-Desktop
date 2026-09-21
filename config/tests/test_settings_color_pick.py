from __future__ import annotations

import unittest

from luminophore_shell.screen_color_picker import ScreenColorPickResult
from luminophore_shell.ui.settings import (
    SettingsView,
    _monitor_palette_colors,
    _monitor_palette_connectors,
)
from luminophore_shell.theme import Palette


class FakeEditor:
    def __init__(self) -> None:
        self.colors: list[str] = []

    def set_color(self, color: str) -> None:
        self.colors.append(color)


class FakeLabel:
    def __init__(self) -> None:
        self.text = ""

    def set_label(self, text: str) -> None:
        self.text = text


class SettingsColorPickTests(unittest.TestCase):
    def test_monitor_palette_rows_keep_connected_order_and_saved_disconnected_rows(self) -> None:
        current = {
            "DP-2": Palette("#111111", "#222222"),
            "DP-1": Palette("#333333", "#444444"),
        }
        saved = {"DP-1": ["#AAAAAA", "#BBBBBB"], "HDMI-A-1": ["#CCCCCC", "#DDDDDD"]}

        self.assertEqual(_monitor_palette_connectors(current, saved), ["DP-2", "DP-1", "HDMI-A-1"])
        self.assertEqual(_monitor_palette_colors("DP-1", saved, "#010101", "#020202"), ("#AAAAAA", "#BBBBBB"))
        self.assertEqual(_monitor_palette_colors("DP-2", saved, "#010101", "#020202"), ("#010101", "#020202"))

    def test_failed_pick_preserves_draft_and_editor(self) -> None:
        view = SettingsView.__new__(SettingsView)
        editor = FakeEditor()
        target = "monitor-palette-0-0"
        view.color_editors = {target: editor}
        view.monitor_color_targets = {target: ("DP-2", 0)}
        view.status = FakeLabel()
        updates: list[tuple[str, str]] = []
        view._set_value = lambda path, value: updates.append((path, value))
        view._sync_screen_picker_buttons = lambda: None

        result = ScreenColorPickResult(False, message="색상 선택을 취소했습니다", error_category="cancelled")
        view._screen_color_picked(target, result)

        self.assertEqual(updates, [])
        self.assertEqual(editor.colors, [])
        self.assertEqual(view.status.text, "색상 선택을 취소했습니다")

    def test_monitor_pick_updates_one_role_in_whole_mapping(self) -> None:
        view = SettingsView.__new__(SettingsView)
        editor = FakeEditor()
        target = "monitor-palette-0-1"
        view.color_editors = {target: editor}
        view.monitor_color_targets = {target: ("DP-2", 1)}
        view.draft = {
            "theme.fixed_primary": "#112233",
            "theme.fixed_secondary": "#AABBCC",
            "theme.fixed_monitor_palettes": {},
        }
        view.status = FakeLabel()
        updates: list[tuple[str, object]] = []
        view._set_value = lambda path, value: updates.append((path, value))
        view._sync_screen_picker_buttons = lambda: None

        result = ScreenColorPickResult(True, "#445566", "화면에서 색상을 가져왔습니다")
        view._screen_color_picked(target, result)

        self.assertEqual(
            updates,
            [("theme.fixed_monitor_palettes", {"DP-2": ["#112233", "#445566"]})],
        )
        self.assertEqual(editor.colors, ["#445566"])


if __name__ == "__main__":
    unittest.main()
