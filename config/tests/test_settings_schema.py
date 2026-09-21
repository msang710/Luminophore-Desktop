from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import unittest

from luminophore_shell.config import (
    AppearanceConfig,
    CompositorConfig,
    InputConfig,
    TouchpadConfig,
    TouchDeviceConfig,
    VirtualKeyboardConfig,
    TabletConfig,
    TabletToolConfig,

    LauncherConfig,
    LayoutConfig,
    LocationConfig,
    MetricsConfig,
    MotionConfig,
    NotificationConfig,
    TaskbarConfig,
    ThemeConfig,
    Threshold,
    SystemThemeConfig,
    WeatherConfig,
    load_config,
)
from luminophore_shell.visual_settings import VisualSettings
from luminophore_shell.settings_schema import (
    CUSTOM_SETTING_PATHS,
    CATEGORIES,
    EXCLUDED_PATHS,
    SETTINGS,
    settings_values,
    specs_for_category,
)


class SettingsSchemaTests(unittest.TestCase):
    def test_every_config_field_is_connected_or_explicitly_excluded(self) -> None:
        expected: set[str] = set()
        for section, config_type in (
            ("layout", LayoutConfig),
            ("theme", ThemeConfig),
            ("compositor", CompositorConfig),
            ("input", InputConfig),
            ("touchpad", TouchpadConfig),
            ("touchdevice", TouchDeviceConfig),
            ("virtualkeyboard", VirtualKeyboardConfig),
            ("tablet", TabletConfig),
            ("tablettool", TabletToolConfig),

            ("motion", MotionConfig),
            ("visual", VisualSettings),
            ("appearance", AppearanceConfig),
            ("system_theme", SystemThemeConfig),
            ("location", LocationConfig),
            ("launcher", LauncherConfig),
            ("taskbar", TaskbarConfig),
            ("notifications", NotificationConfig),
            ("weather", WeatherConfig),
        ):
            expected.update(f"{section}.{item.name}" for item in fields(config_type))
        threshold_names = {"cpu", "gpu", "coolant", "nvme_0700", "nvme_0100"}
        expected.update(
            f"metrics.{item.name}"
            for item in fields(MetricsConfig)
            if item.name not in threshold_names
        )
        expected.update(
            f"metrics.{name}.{item.name}"
            for name in threshold_names
            for item in fields(Threshold)
        )
        paths = [spec.path for spec in SETTINGS]

        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(set(paths) | CUSTOM_SETTING_PATHS | EXCLUDED_PATHS, expected)

    def test_categories_and_values_are_complete(self) -> None:
        config = load_config(Path("luminophore_shell/config.toml"))
        values = settings_values(config)

        self.assertEqual(set(values), {spec.path for spec in SETTINGS})
        self.assertEqual({spec.category for spec in SETTINGS}, {item.category_id for item in CATEGORIES})
        self.assertIsInstance(values["launcher.preferred_actions"], dict)
        self.assertIsInstance(values["launcher.fixed_apps"], list)
        self.assertIsInstance(values["theme.fixed_monitor_palettes"], dict)
        self.assertIsInstance(values["theme.app_icon_aliases"], dict)
        self.assertEqual(values["motion.preset"], "balanced")

    def test_hyprland_schema_matches_typed_allowlist(self) -> None:
        from luminophore_shell.hyprland_settings import HYPRLAND_OPTION_SPECS

        paths = {spec.path for spec in SETTINGS}
        self.assertTrue((set(HYPRLAND_OPTION_SPECS) - {"compositor.blur_enabled", "compositor.blur_size", "compositor.blur_passes"}).issubset(paths))
        self.assertFalse(any("lua" in path or "raw" in path for path in set(HYPRLAND_OPTION_SPECS)))
        kinds = {spec.path: spec.kind for spec in SETTINGS}
        self.assertNotIn("compositor.blur_size", kinds)
        self.assertEqual(kinds["visual.intensity"], "decimal")
        self.assertEqual(kinds["motion.preset"], "choice")
        self.assertEqual(kinds["appearance.wallpaper_provider"], "choice")

    def test_font_settings_use_system_family_dropdowns(self) -> None:
        kinds = {spec.path: spec.kind for spec in SETTINGS}

        self.assertEqual(kinds["theme.body_font"], "font_family")
        self.assertEqual(kinds["theme.numeric_font"], "font_family")
        self.assertEqual(kinds["theme.fixed_monitor_palettes"], "monitor_palette_map")
        self.assertEqual(kinds["theme.app_icon_aliases"], "icon_aliases")

    def test_global_palette_colors_are_snapshot_only_not_visible_controls(self) -> None:
        global_color_paths = {
            "theme.fallback_primary",
            "theme.fallback_secondary",
            "theme.fixed_primary",
            "theme.fixed_secondary",
        }
        palette_controls = {spec.path for spec in specs_for_category("palette")}

        self.assertTrue(global_color_paths.isdisjoint(palette_controls))
        self.assertTrue(global_color_paths.issubset(settings_values(load_config(Path("luminophore_shell/config.toml")))))
        self.assertIn("theme.fixed_monitor_palettes", palette_controls)
        self.assertFalse(any(spec.screen_picker for spec in SETTINGS if spec.path in global_color_paths))


if __name__ == "__main__":
    unittest.main()
