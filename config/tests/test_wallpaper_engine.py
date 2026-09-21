from __future__ import annotations

import unittest

import luminophore_shell.wallpaper_engine as retired


class WallpaperEngineRetirementTests(unittest.TestCase):
    def test_legacy_discovery_and_render_api_is_absent(self) -> None:
        for symbol in (
            "discover_" + "engine_assignments", "parse_" + "engine_argv", "build_" + "render_jobs",
            "render_" + "screenshot", "extract_" + "engine_palettes",
        ):
            self.assertFalse(hasattr(retired, symbol), symbol)


if __name__ == "__main__":
    unittest.main()
