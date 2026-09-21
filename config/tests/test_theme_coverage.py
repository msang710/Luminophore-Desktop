from __future__ import annotations

import unittest

from luminophore_shell.theme_coverage import REQUIRED_THEME_TARGET_IDS, coverage_manifest


class ThemeCoverageTests(unittest.TestCase):
    def test_exact_legacy_template_set_is_resolved(self) -> None:
        manifest = coverage_manifest()
        rows = manifest["targets"]
        self.assertEqual({row["id"] for row in rows}, REQUIRED_THEME_TARGET_IDS)
        self.assertTrue(all(row["coverage_kind"] in {"direct", "toolkit-indirect", "intentional-drop"} for row in rows))
        self.assertEqual(next(row for row in rows if row["id"] == "ghostty")["compiler_output"], "ghostty")
        self.assertEqual(
            next(row for row in rows if row["id"] == "hyprland")["activation_target"],
            "hyprland-settings-transaction",
        )
        self.assertEqual(
            next(row for row in rows if row["id"] == "cursor")["activation_target"],
            "dual Hyprcursor/XCursor transaction",
        )
        for target in ("gimp", "libreoffice", "obs"):
            self.assertEqual(next(row for row in rows if row["id"] == target)["coverage_kind"], "toolkit-indirect")


if __name__ == "__main__":
    unittest.main()
