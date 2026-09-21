from __future__ import annotations

import unittest

from luminophore_shell.appearance_types import AppearanceErrorCategory, TargetCapability
from luminophore_shell.ui.settings_wallpaper import MonitorCard, WallpaperPageState, WallpaperUiPhase


class WallpaperUiTests(unittest.TestCase):
    def test_monitors_and_targets_are_deterministically_ordered(self) -> None:
        state = WallpaperPageState.build(
            (MonitorCard("DP-2", "right"), MonitorCard("DP-1", "left")),
            targets=(TargetCapability("qt", True, "reload"), TargetCapability("gtk", True, "live")),
            phase=WallpaperUiPhase.READY,
        )
        self.assertEqual([item.connector for item in state.monitors], ["DP-1", "DP-2"])
        self.assertEqual([item.target_id for item in state.targets], ["gtk", "qt"])
        self.assertTrue(state.can_apply)

    def test_private_source_path_is_reduced_to_filename(self) -> None:
        card = MonitorCard.from_source_path("DP-1", "/home/private/Pictures/secret.png")
        self.assertEqual(card.source_name, "secret.png")
        self.assertNotIn("/home/private", repr(card))

    def test_offline_and_compile_error_disable_apply(self) -> None:
        card = MonitorCard("DP-1", "left")
        offline = WallpaperPageState.build((card,), phase=WallpaperUiPhase.READY, online=False)
        failed = WallpaperPageState.build(
            (card,), phase=WallpaperUiPhase.ERROR, error=AppearanceErrorCategory.COMPILER_MISSING
        )
        self.assertFalse(offline.can_apply)
        self.assertFalse(failed.can_apply)
        self.assertIn("Matugen", failed.status_text)


if __name__ == "__main__":
    unittest.main()
