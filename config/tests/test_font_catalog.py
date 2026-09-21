from __future__ import annotations

import unittest

from luminophore_shell.font_catalog import font_dropdown_items, installed_font_families, normalize_font_families


class FontCatalogTests(unittest.TestCase):
    def test_families_are_trimmed_casefold_deduplicated_and_sorted(self) -> None:
        self.assertEqual(
            normalize_font_families([" Zed ", "alpha", "ALPHA", "Beta", ""]),
            ("alpha", "Beta", "Zed"),
        )

    def test_installed_current_family_is_selected_by_casefold_name(self) -> None:
        items = font_dropdown_items("beta", ["Alpha", "Beta", "Gamma"])

        self.assertEqual(items.values, ("Alpha", "Beta", "Gamma"))
        self.assertEqual(items.labels, items.values)
        self.assertEqual(items.selected, 1)

    def test_missing_current_family_is_preserved_and_labeled(self) -> None:
        items = font_dropdown_items("Removed Font", ["Alpha", "Beta"])

        self.assertEqual(items.values[0], "Removed Font")
        self.assertEqual(items.labels[0], "Removed Font · 설치되지 않음")
        self.assertEqual(items.selected, 0)

    def test_live_font_map_contains_configured_default_families(self) -> None:
        families = installed_font_families()

        self.assertIn("Noto Sans CJK KR", families)
        self.assertIn("MesloLGS Nerd Font Mono", families)


if __name__ == "__main__":
    unittest.main()
