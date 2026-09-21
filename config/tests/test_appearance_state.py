import tempfile
import unittest
from pathlib import Path

from luminophore_shell.appearance_state import FileAppearanceStateStore
from luminophore_shell.appearance_types import CompiledAppearance, WallpaperProviderName
from luminophore_shell.state import read_palette_state_details


def compiled(primary: str = "#112233", secondary: str = "#445566") -> CompiledAppearance:
    return CompiledAppearance.build(
        {"primary": primary, "secondary": secondary},
        {"primary": primary, "secondary": secondary},
        {"gtk4": b"payload"},
    )


class AppearanceStateTests(unittest.TestCase):
    def test_apply_writes_palette_provider_config_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            palette = root / "palette.json"
            config.write_text('[theme]\npalette_source = "fixed"\n', encoding="utf-8")
            calls: list[str] = []
            store = FileAppearanceStateStore(config, lambda: calls.append("apply"), palette_path=palette, clock=lambda: 10.0)
            store.apply({"DP-1": compiled()}, WallpaperProviderName.HYPRPAPER)
            state = read_palette_state_details(palette)
            self.assertEqual(state.provider, "hyprpaper")
            self.assertEqual(state.entries["DP-1"].palette.primary, "#112233")
            self.assertIn('palette_source = "hyprpaper"', config.read_text())
            self.assertEqual(calls, ["apply"])

    def test_restore_is_exact_for_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            palette = root / "palette.json"
            config.write_bytes(b'[theme]\npalette_source = "fixed"\n# keep\n')
            palette.write_bytes(b"old-palette")
            calls: list[str] = []
            store = FileAppearanceStateStore(
                config, lambda: calls.append("apply"), runtime_restore=lambda: calls.append("restore"),
                palette_path=palette,
            )
            snapshot = store.snapshot()
            store.apply({"DP-1": compiled()}, WallpaperProviderName.AWWW)
            store.restore(snapshot)
            self.assertEqual(config.read_bytes(), b'[theme]\npalette_source = "fixed"\n# keep\n')
            self.assertEqual(palette.read_bytes(), b"old-palette")
            self.assertEqual(calls, ["apply", "restore"])

    def test_restore_removes_new_palette_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            palette = root / "palette.json"
            config.write_text('[theme]\npalette_source = "fixed"\n', encoding="utf-8")
            store = FileAppearanceStateStore(config, lambda: None, palette_path=palette)
            snapshot = store.snapshot()
            store.apply({"DP-1": compiled()}, WallpaperProviderName.HYPRPAPER)
            store.restore(snapshot)
            self.assertFalse(palette.exists())


if __name__ == "__main__":
    unittest.main()
