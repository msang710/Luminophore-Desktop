from dataclasses import asdict
from pathlib import Path
import tempfile
import tomllib
import unittest

from luminophore_shell.config import ConfigConflictError, ConfigError, config_digest, load_config, load_config_text, write_config_patch
from luminophore_shell.visual_settings import VisualSettings, resolve_visual_config


class VisualConfigTests(unittest.TestCase):
    def test_loading_legacy_file_uses_semantic_defaults_without_rewriting(self):
        source = Path("tests/fixtures/legacy-config.toml").read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source)
            self.assertEqual(load_config(path).visual, VisualSettings())
            self.assertEqual(path.read_text(), source)

    def test_valid_partial_table_is_normalized_and_does_not_change_input(self):
        raw = {"breathing": False, "intensity": 8.0}
        result, reason = resolve_visual_config(raw)
        self.assertEqual(result, VisualSettings(breathing=False, intensity=3.0))
        self.assertEqual(reason, "none")
        self.assertEqual(raw, {"breathing": False, "intensity": 8.0})
        self.assertEqual(load_config_text('[visual]\nbreathing=false\nintensity=-2').visual.intensity, 0.0)

    def test_invalid_bundle_restores_all_defaults_with_reason(self):
        for fields, reason in (({"schema_version": 0}, "schema"), ({"schema_version": True}, "malformed"),
                               ({"preset": "old"}, "preset"), ({"intensity": float("nan")}, "intensity"),
                               ({"intensity": float("inf")}, "intensity"), ({"intensity": 10**1000}, "intensity"),
                               ({"intensity": True}, "malformed"), ({"kernel": 4}, "malformed")):
            raw = {"enabled": False, "breathing": False, "intensity": 2.0, **fields}
            with self.subTest(fields=fields):
                self.assertEqual(resolve_visual_config(raw), (VisualSettings(), reason))
        with self.assertLogs("luminophore-shell", level="WARNING") as logs:
            loaded = load_config_text('[visual]\nschema_version=0\nenabled=false\nbreathing=false\nintensity=2')
        self.assertEqual(loaded.visual, VisualSettings())
        self.assertIn("reason=schema", logs.output[0])

    def test_first_save_adds_complete_semantic_table_and_preserves_legacy_text(self):
        source = Path("tests/fixtures/legacy-config.toml").read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source)
            loaded = write_config_patch(path, {"visual.intensity": 1.5, "visual.breathing": False}, config_digest(path))
            self.assertEqual(loaded.visual, VisualSettings(intensity=1.5, breathing=False))
            saved = path.read_text()
            self.assertTrue(saved.startswith(source))
            self.assertEqual(tomllib.loads(saved)["visual"], asdict(loaded.visual))
            self.assertEqual(load_config(path).visual, loaded.visual)

    def test_invalid_existing_table_can_be_saved_as_complete_valid_bundle(self):
        source = '[visual]\nschema_version=0\nenabled=false\nintensity=2\n\n[layout]\nradius=12\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(source)
            loaded = write_config_patch(path, {"visual.breathing": False}, config_digest(path))
            self.assertEqual(loaded.visual, VisualSettings(breathing=False))
            self.assertIn('[layout]\nradius=12\n', path.read_text())
            self.assertEqual(tomllib.loads(path.read_text())["visual"], asdict(loaded.visual))

    def test_unrewritable_visual_tables_fail_without_partial_save(self):
        for source in ('visual = false\n', 'visual.enabled = false\n',
                       '[visual]\nenabled=false\n[visual.kernel]\nsize=4\n', '[visual\n'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                path.write_text(source)
                with self.assertRaises(ConfigError):
                    write_config_patch(path, {"visual.breathing": False}, config_digest(path))
                self.assertEqual(path.read_text(), source)

    def test_semantic_bundle_drives_existing_shell_style_consumers(self):
        source = '[theme]\noutline_glow_intensity=2\nanimate_glow=true\noutline_glow_radius=32\n[visual]\nenabled=false\n'
        with self.assertLogs("luminophore-shell", level="WARNING"):
            loaded = load_config_text(source)
        self.assertEqual(loaded.theme.outline_glow_intensity, 0.0)
        self.assertFalse(loaded.theme.animate_glow)
        self.assertEqual(loaded.theme.outline_glow_radius, 8)

    def test_semantic_table_wins_before_retired_glow_validation(self):
        source = '[theme]\noutline_glow_intensity="obsolete"\noutline_glow_radius=-1\nanimate_glow="old"\n[visual]\nintensity=1.5\n'
        with self.assertLogs("luminophore-shell", level="WARNING"):
            loaded = load_config_text(source)
        self.assertEqual(loaded.theme.outline_glow_intensity, 1.5)
        self.assertEqual(loaded.theme.outline_glow_radius, 8)
        self.assertTrue(loaded.theme.animate_glow)

    def test_invalid_save_and_digest_conflict_leave_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            source = '[layout]\nradius=12\n'
            path.write_text(source)
            for changes in ({"visual.enabled": 1}, {"visual.preset": "old"}, {"visual.kernel": 3},
                            {"visual.schema_version": 0}, {"visual.intensity": float("nan")}):
                with self.subTest(changes=changes), self.assertRaises(ConfigError):
                    write_config_patch(path, changes, config_digest(path))
                self.assertEqual(path.read_text(), source)
            with self.assertRaises(ConfigConflictError):
                write_config_patch(path, {"visual.enabled": False}, "old-digest")
            self.assertEqual(path.read_text(), source)


if __name__ == "__main__":
    unittest.main()
