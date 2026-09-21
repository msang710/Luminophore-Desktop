from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from luminophore_shell.appearance_compiler import AppearanceCompilerError
from luminophore_shell.appearance_types import (
    AppearanceCompileRequest,
    AppearanceMode,
    AppearanceSource,
    AppearanceSourceKind,
    CompiledAppearance,
)
from luminophore_shell.hyprland_settings import HyprlandPaletteTransaction, SemanticPalette
from luminophore_shell.matugen import MatugenAppearanceCompiler
from luminophore_shell.system_theme import MatugenThemeBackend


SCHEME = {
    "source_color": "#556677",
    "primary": "#AFC6FF",
    "on_primary": "#102A55",
    "secondary": "#BBC6E4",
    "on_secondary": "#253048",
    "surface": "#1B1B1F",
    "surface_container": "#202024",
    "on_surface": "#E3E2E6",
    "on_surface_variant": "#C4C6D0",
    "error": "#FFB4AB",
    "on_error": "#690005",
    "outline": "#8E9099",
}
OUTPUTS = {
    "gtk3": b"gtk3",
    "gtk4": b"gtk4",
    "qt": b"qt",
    "kde": b"kde",
    "kitty": b"kitty",
    "alacritty": b"alacritty",
    "btop": b"btop",
    "ghostty": b"ghostty",
}


def compiled() -> CompiledAppearance:
    return CompiledAppearance.build(
        SCHEME,
        {
            "primary": SCHEME["primary"],
            "secondary": SCHEME["secondary"],
            "background": SCHEME["surface"],
            "foreground": SCHEME["on_surface"],
            "muted": SCHEME["on_surface_variant"],
        },
        OUTPUTS,
    )


class RecordingRuntime:
    def __init__(self) -> None:
        self.read_calls = 0
        self.apply_calls = 0

    def read_options(self, names):
        self.read_calls += 1
        return {name: "rgba(000000ff)" for name in names}

    def apply_options(self, values):
        self.apply_calls += 1


class AppearanceThemeHandoffTests(unittest.TestCase):
    def test_one_generation_and_semantic_payload_cross_both_handoffs_exactly(self) -> None:
        artifact = compiled()
        preview = MatugenThemeBackend().preview_compiled(artifact, "DP-2", "config-digest", "dark")
        palette = SemanticPalette.from_scheme(artifact.generation_id, preview.hyprland_settings)

        self.assertEqual(preview.semantic.generation_id, artifact.generation_id)
        self.assertEqual(palette.generation_id, artifact.generation_id)
        self.assertEqual(preview.hyprland_settings, {
            "primary": SCHEME["primary"],
            "surface_container": SCHEME["surface_container"],
            "secondary": SCHEME["secondary"],
            "error": SCHEME["error"],
        })
        self.assertEqual(palette.generation_id, artifact.generation_id)
        values = dict(palette.values)
        self.assertEqual(values['general:col.active_border'], 'rgba(afc6ffff)')
        self.assertEqual(values['general:col.inactive_border'], 'rgba(202024ff)')
        self.assertEqual(set(values), {'general:col.active_border', 'general:col.inactive_border'})

    def test_missing_semantic_token_fails_before_any_runtime_or_file_mutation(self) -> None:
        runtime = RecordingRuntime()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "luminophore_semantic_palette.lua"
            with self.assertRaisesRegex(ValueError, "surface_container"):
                palette = SemanticPalette.from_scheme(compiled().generation_id, {
                    "primary": SCHEME["primary"],
                    "secondary": SCHEME["secondary"],
                    "error": SCHEME["error"],
                })
                HyprlandPaletteTransaction(runtime, path).apply(palette)

            self.assertFalse(path.exists())
            self.assertEqual(runtime.read_calls, 0)
            self.assertEqual(runtime.apply_calls, 0)

    def test_compiler_has_no_background_only_hyprland_output(self) -> None:
        self.assertNotIn("hyprland", MatugenAppearanceCompiler.SUPPORTED_OUTPUTS)
        request = AppearanceCompileRequest(
            AppearanceSource(AppearanceSourceKind.COLOR, "#89511E"),
            AppearanceMode.DARK,
            ("hyprland",),
        )
        with self.assertRaises(AppearanceCompilerError) as caught:
            MatugenAppearanceCompiler(Path("/bin/true")).compile(request)
        self.assertEqual(caught.exception.category, "compiler_incompatible")


if __name__ == "__main__":
    unittest.main()
