from __future__ import annotations

import unittest

from luminophore_shell.ui.settings_hyprland import HyprlandPageState


class HyprlandUiTests(unittest.TestCase):
    def test_unavailable_control_is_visible_but_disabled(self) -> None:
        state = HyprlandPageState.build(
            {"decoration:rounding": 12, "misc:vrr": 1},
            {"decoration:rounding": "모서리", "misc:vrr": "VRR"},
            unavailable=("misc:vrr",),
            online=True,
        )
        self.assertTrue(state.enabled("decoration:rounding"))
        self.assertFalse(state.enabled("misc:vrr"))
        self.assertIn("지원하지", state.controls[1].reason)

    def test_busy_conflict_and_offline_are_distinct(self) -> None:
        values = {"decoration:rounding": 12}
        labels = {"decoration:rounding": "모서리"}
        self.assertIn("외부", HyprlandPageState.build(values, labels, online=True, conflict=True).banner)
        self.assertIn("적용", HyprlandPageState.build(values, labels, online=True, busy=True).banner)
        self.assertIn("다음 시작", HyprlandPageState.build(values, labels, online=False).banner)


if __name__ == "__main__":
    unittest.main()
