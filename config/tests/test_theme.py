from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from luminophore_shell.config import ThemeConfig
from luminophore_shell.matugen import generated_palette, parse_matugen_json
from luminophore_shell.state import read_palette_state, read_palette_state_details, write_palette_state
from tests.test_matugen import fixture as matugen_fixture
from luminophore_shell.theme import Palette, build_css, extract_palette_image, resolve_palettes


class ThemePaletteTests(unittest.TestCase):
    def test_fixed_source_is_global_for_all_monitors(self) -> None:
        config = ThemeConfig(palette_source="fixed", fixed_primary="#112233", fixed_secondary="#AABBCC")

        palettes = resolve_palettes(config, ["DP-1", "DP-2"], {}, {})

        self.assertEqual(palettes, {0: Palette("#112233", "#AABBCC"), 1: Palette("#112233", "#AABBCC")})

    def test_fixed_source_prefers_connector_overrides_and_falls_back_globally(self) -> None:
        config = ThemeConfig(
            palette_source="fixed",
            fixed_primary="#112233",
            fixed_secondary="#AABBCC",
            fixed_monitor_palettes=(("DP-2", "#445566", "#DDEEFF"),),
        )

        palettes = resolve_palettes(config, ["DP-1", "DP-2", "HDMI-A-1"], {}, {})

        self.assertEqual(palettes[0], Palette("#112233", "#AABBCC"))
        self.assertEqual(palettes[1], Palette("#445566", "#DDEEFF"))
        self.assertEqual(palettes[2], Palette("#112233", "#AABBCC"))

    def test_static_snapshot_sources_share_per_connector_state_contract(self) -> None:
        saved = {
            "DP-1": Palette("#102030", "#405060"),
            "DP-2": Palette("#607080", "#90A0B0"),
        }

        for source in ("hyprpaper", "awww"):
            with self.subTest(source=source):
                palettes = resolve_palettes(ThemeConfig(palette_source=source), ["DP-1", "DP-2"], {}, saved)
                self.assertEqual(palettes, {0: saved["DP-1"], 1: saved["DP-2"]})

    def test_palette_state_round_trips_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "palette.json"
            expected = {"DP-1": Palette("#123456", "#ABCDEF")}

            write_palette_state(expected, 123.5, path)
            captured, palettes = read_palette_state(path)

            self.assertEqual(captured, 123.5)
            self.assertEqual(palettes, expected)

    def test_palette_state_v2_round_trips_scheme_without_image_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "palette.json"
            generated = generated_palette(parse_matugen_json(matugen_fixture()))

            write_palette_state(
                {"DP-1": generated.palette},
                123.5,
                path,
                schemes={"DP-1": generated.scheme},
                provider="hyprpaper",
            )
            state = read_palette_state_details(path)

            self.assertEqual(state.provider, "hyprpaper")
            self.assertEqual(state.entries["DP-1"].scheme, generated.scheme)
            self.assertNotIn("/private/wallpaper.png", path.read_text(encoding="utf-8"))

    def test_palette_state_v1_is_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "palette.json"
            path.write_text(
                '{"version":1,"captured_at":1,"palettes":{"DP-1":{"primary":"#123456","secondary":"#ABCDEF"}}}',
                encoding="utf-8",
            )

            captured, palettes = read_palette_state(path)

            self.assertIsNone(captured)
            self.assertEqual(palettes, {})

    def test_extract_palette_image_returns_distinct_luminophore_colors(self) -> None:
        image = Image.new("RGB", (100, 100), "#9A4D10")
        for x in range(50, 100):
            for y in range(100):
                image.putpixel((x, y), (10, 80, 160))

        palette = extract_palette_image(image, Palette("#000001", "#000002"))

        self.assertNotEqual(palette.primary, palette.secondary)
        self.assertNotEqual(palette, Palette("#000001", "#000002"))

    def test_luminophore_css_does_not_force_foreground_on_native_popovers(self) -> None:
        css = build_css(ThemeConfig(), {0: Palette("#123456", "#ABCDEF")})

        wildcard = css.split("* {", 1)[1].split("}", 1)[0]
        self.assertNotIn("color:", wildcard)
        self.assertIn("popover.luminophore-popover > contents", css)
        self.assertIn("background: rgba(8, 11, 16, 0.96)", css)
        self.assertIn("color: #F3F7FA", css)

    def test_outline_settings_keep_palette_border_and_surface_wash_without_css_glow(self) -> None:
        css = build_css(
            ThemeConfig(outline_width=9, outline_glow_intensity=2.5, outline_glow_radius=24),
            {0: Palette("#123456", "#ABCDEF")},
        )

        self.assertIn("border: 9px solid #78DCE8", css)
        self.assertIn("border-color: #123456", css)
        panel_rule = css.split(".palette-0 .luminophore-panel {", 1)[1].split("}", 1)[0]
        self.assertIn(
            "linear-gradient(to bottom, rgba(18, 52, 86, 0.045), rgba(18, 52, 86, 0.018))",
            panel_rule,
        )
        self.assertNotIn("box-shadow", css)
        self.assertNotIn("luminophore-shimmer", css)

    def test_zero_outline_glow_keeps_border_visible_and_removes_surface_wash(self) -> None:
        css = build_css(
            ThemeConfig(outline_width=8, outline_glow_intensity=0, outline_glow_radius=32),
            {0: Palette("#123456", "#ABCDEF")},
        )

        self.assertIn("border: 8px solid #78DCE8", css)
        self.assertIn("border-color: #123456", css)
        panel_rule = css.split(".palette-0 .luminophore-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("background-image: none", panel_rule)
        self.assertNotIn("box-shadow", css)
        self.assertNotIn("@keyframes luminophore-shimmer", css)

    def test_css_glow_renderer_is_fully_removed(self) -> None:
        css = build_css(
            ThemeConfig(outline_glow_intensity=1.0, outline_glow_radius=20),
            {0: Palette("#123456", "#ABCDEF")},
        )

        self.assertNotIn("box-shadow", css)
        self.assertNotIn("luminophore-shimmer", css)
        self.assertNotIn("animation-delay", css)

    def test_corner_frame_transparently_reserves_css_border_and_uses_semantic_tokens(self) -> None:
        css = build_css(
            ThemeConfig(outline_width=5),
            {0: Palette("#1B2330", "#AB9DF2")},
        )

        self.assertIn(".palette-0 .luminophore-panel.gsk-luminophore-frame", css)
        self.assertIn("border-color: transparent", css)
        self.assertIn(".palette-0 .luminophore-key-primary", css)
        self.assertIn(".palette-0 .luminophore-symbol-secondary", css)
        self.assertIn("text-shadow:", css)
        self.assertIn("-gtk-icon-shadow:", css)

    def test_font_families_are_quoted_and_escaped_for_gtk_css(self) -> None:
        css = build_css(
            ThemeConfig(body_font='Body "Quoted"', numeric_font="Path\\Mono"),
            {0: Palette("#123456", "#ABCDEF")},
        )

        self.assertIn('font-family: "Body \\"Quoted\\""', css)
        self.assertIn('font-family: "Path\\\\Mono"', css)

    def test_accessibility_css_scales_once_and_strengthens_contrast(self) -> None:
        css = build_css(
            ThemeConfig(ui_scale=1.25, high_contrast=True, backdrop_opacity=0.4, outline_width=1),
            {0: Palette("#123456", "#ABCDEF")},
        )
        self.assertIn("window.luminophore-surface { background: transparent; font-size: 125.0%; }", css)
        self.assertEqual(css.count("font-size: 125.0%"), 1)
        self.assertIn("background-color: rgba(8, 11, 16, 0.900)", css)
        self.assertIn("color: #FFFFFF", css)
        self.assertIn("border: 2px solid", css)

    def test_osd_progress_has_explicit_centerable_height(self) -> None:
        css = build_css(ThemeConfig(), {0: Palette("#123456", "#ABCDEF")})
        self.assertIn(".luminophore-osd > progressbar.osd-progress", css)
        self.assertIn("min-height: 8px", css)
        self.assertIn(".luminophore-osd > .osd-icon", css)
        self.assertIn(".luminophore-osd > .osd-value", css)
        osd_value = css.split(".luminophore-osd > .osd-value {", 1)[1].split("}", 1)[0]
        self.assertIn("font-size: 13px", osd_value)
        self.assertIn("font-weight: 700", osd_value)
        osd_base = css.split(".luminophore-osd > progressbar.osd-progress", 1)[0]
        self.assertNotIn("min-width: 4.5em", osd_base)


if __name__ == "__main__":
    unittest.main()
