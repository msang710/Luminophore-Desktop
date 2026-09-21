from __future__ import annotations

import unittest

from luminophore_shell.ui.settings_bindings import BindingPageState, BindingRow, normalize_chord


class BindingUiTests(unittest.TestCase):
    def test_chord_normalization_and_collision(self) -> None:
        self.assertEqual(normalize_chord("super + shift + z"), "SHIFT+SUPER+Z")
        state = BindingPageState.build((
            BindingRow("settings", "설정", "SUPER+Z", recovery=True),
            BindingRow("other", "기타", "super+z"),
        ))
        self.assertFalse(state.can_apply)
        self.assertTrue(state.collisions)

    def test_recovery_binding_cannot_be_removed(self) -> None:
        state = BindingPageState.build((BindingRow("settings", "설정", "", recovery=True),))
        self.assertFalse(state.can_apply)
        self.assertIn("복구", state.error_text)


if __name__ == "__main__":
    unittest.main()
