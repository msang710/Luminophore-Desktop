from __future__ import annotations

import unittest

from luminophore_shell.emoji import EMOJI, search_emoji


class EmojiTests(unittest.TestCase):
    def test_bundled_metadata_supports_korean_and_english_search(self) -> None:
        self.assertGreaterEqual(len(EMOJI), 30)
        self.assertEqual(search_emoji("rocket")[0].glyph, "🚀")
        self.assertEqual(search_emoji("경고")[0].glyph, "⚠️")


if __name__ == "__main__":
    unittest.main()
