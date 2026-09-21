from __future__ import annotations

import colorsys
import unittest

from luminophore_shell.theme import Palette
from luminophore_shell.visual_tokens import contrast_ratio, derive_visual_tokens


class VisualTokenTests(unittest.TestCase):
    def test_dark_primary_gets_readable_face_without_changing_raw(self) -> None:
        tokens = derive_visual_tokens(Palette("#1B2330", "#421C51"), 0.4)

        self.assertEqual(tokens.raw_primary, "#1B2330")
        self.assertNotEqual(tokens.face_primary, tokens.raw_primary)
        self.assertGreaterEqual(contrast_ratio(tokens.face_primary, "#080B10"), 4.5)

    def test_bright_accent_is_not_changed_unnecessarily(self) -> None:
        tokens = derive_visual_tokens(Palette("#78DCE8", "#AB9DF2"), 0.4)

        self.assertEqual(tokens.face_primary, "#78DCE8")
        self.assertEqual(tokens.face_secondary, "#AB9DF2")

    def test_face_keeps_raw_hue(self) -> None:
        tokens = derive_visual_tokens(Palette("#C01C28", "#244A9F"), 0.4)

        def hue(value: str) -> float:
            clean = value[1:]
            rgb = tuple(int(clean[index : index + 2], 16) / 255 for index in (0, 2, 4))
            return colorsys.rgb_to_hls(*rgb)[0]

        self.assertAlmostEqual(hue(tokens.raw_primary), hue(tokens.face_primary), delta=0.01)

    def test_monitor_palettes_remain_independent(self) -> None:
        left = derive_visual_tokens(Palette("#C01C28", "#AB9DF2"), 0.4)
        right = derive_visual_tokens(Palette("#16A7D9", "#65E6A5"), 0.4)

        self.assertNotEqual(left.raw_primary, right.raw_primary)
        self.assertNotEqual(left.face_primary, right.face_primary)

    def test_malformed_colour_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            derive_visual_tokens(Palette("#1234", "#ABCDEF"), 0.4)


if __name__ == "__main__":
    unittest.main()
