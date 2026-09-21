from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest
import subprocess
import os
import sys

from luminophore_shell.config import (
    ConfigConflictError,
    ConfigError,
    config_digest,
    load_config,
    load_config_text,
    write_config_patch,
    write_taskbar_pins,
    write_theme_source,
)


def _replace_scalar(source: str, key: str, literal: str) -> str:
    updated, count = re.subn(rf"(?m)^{re.escape(key)}\s*=.*$", f"{key} = {literal}", source)
    if count != 1:
        raise AssertionError(f"expected exactly one {key}, found {count}")
    return updated


class ConfigWriteTests(unittest.TestCase):
    def test_installed_session_uses_user_config_outside_release(self):
        result = subprocess.run([sys.executable, '-c',
            'from luminophore_shell.config import CONFIG_PATH; print(CONFIG_PATH)'],
            env={**os.environ, 'LUMINOPHORE_CONFIG_ROOT': '/tmp/luminophore-test-config'},
            capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '/tmp/luminophore-test-config/settings.toml')

    def test_hyprland_settings_round_trip_and_keep_legacy_blur_paths(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")
            loaded = write_config_patch(path, {
                "compositor.gaps_in": 8,
                "compositor.gaps_out": 12,
                "compositor.border_size": 4,
                "compositor.rounding": 16,
                "compositor.active_opacity": 0.95,
                "compositor.inactive_opacity": 0.8,
                "compositor.dim_special": 0.4,
                "compositor.blur_enabled": False,
                "compositor.blur_size": 7,
                "compositor.blur_passes": 2,
                "compositor.vrr": 2,
                "motion.enabled": True,
                "motion.preset": "smooth",
                "motion.speed": 1.4,
            }, config_digest(path))

            self.assertEqual((loaded.compositor.gaps_in, loaded.compositor.gaps_out), (8, 12))
            self.assertEqual((loaded.compositor.blur_size, loaded.compositor.blur_passes), (7, 2))
            self.assertEqual((loaded.motion.preset, loaded.motion.speed), ("smooth", 1.4))
            self.assertEqual(load_config(path), loaded)

    def test_wallpaper_provider_is_typed_and_independent_from_palette_source(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")
            loaded = write_config_patch(path, {
                "appearance.wallpaper_provider": "awww",
                "theme.palette_source": "fixed",
            }, config_digest(path))
            self.assertEqual(loaded.appearance.wallpaper_provider, "awww")
            self.assertEqual(loaded.theme.palette_source, "fixed")
            with self.assertRaisesRegex(ConfigError, "wallpaper_provider"):
                write_config_patch(path, {"appearance.wallpaper_provider": "waypaper"}, config_digest(path))

    def test_hyprland_setting_ranges_and_cross_fields_fail_closed(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        cases = (
            ({"compositor.gaps_in": 101}, "gaps_in"),
            ({"compositor.vrr": 7}, "vrr"),
            ({"motion.preset": "raw"}, "preset"),
            ({"compositor.active_opacity": 0.5, "compositor.inactive_opacity": 0.8}, "inactive_opacity"),
            ({"motion.enabled": False, "motion.preset": "custom"}, "custom requires"),
        )
        for changes, error in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, error):
                    write_config_patch(path, changes, config_digest(path))
                self.assertEqual(path.read_text(encoding="utf-8"), source)
    def test_legacy_weather_location_keys_are_rejected(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        source = source.replace("[weather]\n", "[weather]\nlatitude = 37.5\n")

        with self.assertRaisesRegex(ConfigError, "weather: unknown keys: latitude"):
            load_config_text(source)

    def test_city_location_fields_are_written_atomically(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")
            loaded = write_config_patch(path, {
                "location.automatic": False,
                "location.latitude": 35.18,
                "location.longitude": 129.08,
                "location.timezone": "Asia/Seoul",
            }, config_digest(path))
            self.assertFalse(loaded.location.automatic)
            self.assertEqual((loaded.location.latitude, loaded.location.longitude), (35.18, 129.08))

    def test_generic_patch_updates_scalars_lists_and_mapping(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")

            loaded = write_config_patch(
                path,
                {
                    "layout.edge_margin": 31,
                    "theme.outline_width": 3,
                    "theme.outline_glow_intensity": 0.75,
                    "theme.outline_glow_radius": 24,
                    "theme.animate_glow": True,
                    "theme.audio_spectrum_enabled": True,
                    "compositor.blur_size": 7,
                    "notifications.dnd_allowlist": ["music", "calls"],
                    "launcher.preferred_actions": {
                        "org.example.Settings.desktop": "Settings",
                        "org.example.App.desktop": "NewWindow",
                    },
                    "theme.app_icon_theme": "system",
                    "theme.app_icon_aliases": {
                        "com.visualstudio.code.oss": "visual-studio-code",
                        "org.example.App": "example-app",
                    },
                },
                config_digest(path),
            )

            self.assertEqual(loaded.layout.edge_margin, 31)
            self.assertEqual(loaded.theme.outline_width, 3)
            self.assertEqual(loaded.theme.outline_glow_intensity, 0.75)
            self.assertEqual(loaded.theme.outline_glow_radius, 24)
            self.assertTrue(loaded.theme.animate_glow)
            self.assertTrue(loaded.theme.audio_spectrum_enabled)
            self.assertEqual(loaded.compositor.blur_size, 7)
            self.assertEqual(loaded.notifications.dnd_allowlist, ("music", "calls"))
            self.assertEqual(dict(loaded.launcher.preferred_actions)["org.example.App.desktop"], "NewWindow")
            self.assertEqual(loaded.theme.app_icon_theme, "system")
            self.assertEqual(dict(loaded.theme.app_icon_aliases)["org.example.App"], "example-app")
            self.assertIn("[metrics.nvme_0100]", path.read_text(encoding="utf-8"))

    def test_monitor_fixed_palettes_round_trip_and_missing_table_is_added(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        source = source.replace("[theme.fixed_monitor_palettes]\n\n", "")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")

            loaded = write_config_patch(
                path,
                {
                    "theme.fixed_monitor_palettes": {
                        "DP-1": ["#112233", "#AABBCC"],
                        "DP-2": ["#445566", "#DDEEFF"],
                    },
                },
                config_digest(path),
            )

            self.assertEqual(
                loaded.theme.fixed_monitor_palettes,
                (("DP-1", "#112233", "#AABBCC"), ("DP-2", "#445566", "#DDEEFF")),
            )
            saved = path.read_text(encoding="utf-8")
            self.assertIn("[theme.fixed_monitor_palettes]", saved)
            self.assertIn('"DP-2" = ["#445566", "#DDEEFF"]', saved)

    def test_monitor_fixed_palette_rejects_invalid_or_equal_colors(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        source = re.sub(
            r"(?ms)(^\[theme\.fixed_monitor_palettes\]\n).*?(?=^\[)",
            r"\1\n",
            source,
        )
        for colors, message in (
            ('["cyan", "#AABBCC"]', "colors must be #RRGGBB"),
            ('["#112233", "#112233"]', "primary and secondary must differ"),
            ('["#112233"]', "must contain primary and secondary"),
        ):
            with self.subTest(colors=colors), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                candidate = source.replace(
                    "[theme.fixed_monitor_palettes]\n",
                    f'[theme.fixed_monitor_palettes]\n"DP-1" = {colors}\n',
                )
                path.write_text(candidate, encoding="utf-8")

                with self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_generic_patch_rejects_external_change(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")
            digest = config_digest(path)
            path.write_text(source + "\n# external edit\n", encoding="utf-8")

            with self.assertRaises(ConfigConflictError):
                write_config_patch(path, {"layout.edge_margin": 30}, digest)

    def test_invalid_generic_patch_does_not_touch_file(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, r"contain \{query\}"):
                write_config_patch(path, {"launcher.web_url": "https://example.com/search"}, config_digest(path))

            self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_retired_drop_settings_load_with_diagnostic_without_rewriting_file(self):
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        source = source.replace("[layout]", "[layout]\ndrop_halo = 24\nmaximize_drop_width = 560")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")
            with self.assertLogs("luminophore-shell", level="WARNING") as logs:
                loaded = load_config(path)
            self.assertEqual(sum("is retired and ignored" in line for line in logs.output), 2)
            self.assertFalse(hasattr(loaded.layout, "drop_halo"))
            self.assertFalse(hasattr(loaded.layout, "maximize_drop_width"))
            self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_pin_update_preserves_other_sections_and_reloads(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")
            original_fixed_apps = load_config(path).launcher.fixed_apps
            write_taskbar_pins(path, ("firefox", "com.mitchellh.ghostty"))
            loaded = load_config(path)
            self.assertEqual(loaded.taskbar.pinned, ("firefox", "com.mitchellh.ghostty"))
            self.assertEqual(loaded.launcher.fixed_apps, original_fixed_apps)
            self.assertEqual(loaded.launcher.preferred_actions, ())
            self.assertEqual(loaded.layout.weather_width, 560)
            self.assertIn("[weather]", path.read_text(encoding="utf-8"))

    def test_preferred_actions_reject_non_string_values(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        source = source.replace(
            "[launcher.preferred_actions]",
            '[launcher.preferred_actions]\n"org.example.App.desktop" = 7',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "string app and action IDs"):
                load_config(path)

    def test_theme_source_write_is_atomic_and_preserves_nested_tables(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")

            write_theme_source(path, "fixed")

            loaded = load_config(path)
            self.assertEqual(loaded.theme.palette_source, "fixed")
            self.assertEqual(loaded.launcher.preferred_actions, ())

    def test_theme_accepts_static_snapshot_sources(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")

            for provider in ("hyprpaper", "awww"):
                write_theme_source(path, provider)
                self.assertEqual(load_config(path).theme.palette_source, provider)

    def test_theme_rejects_invalid_fixed_colors(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        source = _replace_scalar(source, "fixed_primary", '"cyan"')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source, encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "fixed_primary must be #RRGGBB"):
                load_config(path)

    def test_app_icon_theme_and_alias_target_are_validated(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source.replace(
                'app_icon_theme = "luminophore-shell-arcticons"',
                'app_icon_theme = "global"',
            ), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "app_icon_theme"):
                load_config(path)
            path.write_text(source.replace(
                '"com.visualstudio.code.oss" = "visual-studio-code"',
                '"com.visualstudio.code.oss" = "../../escape"',
            ), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "freedesktop icon names"):
                load_config(path)

    def test_system_theme_defaults_dark_and_rejects_unknown_mode(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source.replace('mode = "dark"', 'mode = "auto"'), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "system_theme.mode must be dark or light"):
                load_config(path)

    def test_compositor_blur_values_must_be_positive(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(_replace_scalar(source, "blur_passes", "0"), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "must be integers of at least 1"):
                load_config(path)

    def test_compositor_blur_values_must_be_integers(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(_replace_scalar(source, "blur_size", "5.5"), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "must be integers of at least 1"):
                load_config(path)

    def test_outline_width_glow_intensity_and_radius_boundaries(self) -> None:
        source = Path("tests/fixtures/legacy-config.toml").read_text(encoding="utf-8")
        invalid_values = (
            ("outline_width", "0"),
            ("outline_width", "13"),
            ("outline_width", "1.5"),
            ("outline_glow_intensity", "-0.1"),
            ("outline_glow_intensity", "3.1"),
            ("outline_glow_radius", "0"),
            ("outline_glow_radius", "65"),
            ("outline_glow_radius", "1.5"),
        )
        for key, literal in invalid_values:
            with self.subTest(key=key, value=literal), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                path.write_text(_replace_scalar(source, key, literal), encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, key):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
