from __future__ import annotations

import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from luminophore_shell.appearance_compiler import AppearanceCompilerError
from luminophore_shell.appearance_types import (
    AppearanceCompileRequest,
    AppearanceMode,
    AppearanceSource,
    AppearanceSourceKind,
)
from luminophore_shell.matugen import (
    MatugenAppearanceCompiler,
    MatugenError,
    MatugenPaletteBackend,
    parse_matugen_json,
)


def fixture() -> dict[str, object]:
    pairs = {
        "source_color": ("#556677", "#556677"),
        "primary": ("#AFC6FF", "#34558A"),
        "on_primary": ("#102A55", "#FFFFFF"),
        "secondary": ("#BBC6E4", "#535E78"),
        "on_secondary": ("#253048", "#FFFFFF"),
        "surface": ("#1B1B1F", "#FEFBFF"),
        "surface_container": ("#202024", "#F3F0F4"),
        "on_surface": ("#E3E2E6", "#1B1B1F"),
        "on_surface_variant": ("#C4C6D0", "#44474F"),
        "error": ("#FFB4AB", "#BA1A1A"),
        "on_error": ("#690005", "#FFFFFF"),
        "outline": ("#8E9099", "#74777F"),
    }
    return {
        "image": "/private/wallpaper.png",
        "colors": {
            name: {"dark": {"color": dark}, "light": {"color": light}, "default": {"color": dark}}
            for name, (dark, light) in pairs.items()
        },
    }


class MatugenTests(unittest.TestCase):
    def test_v4_schema_is_normalized_and_private_image_is_removed(self) -> None:
        scheme = parse_matugen_json(json.dumps(fixture()))

        self.assertEqual(scheme.modes["dark"].colors["primary"], "#AFC6FF")
        self.assertEqual(scheme.modes["light"].colors["primary"], "#34558A")
        self.assertNotIn("image", scheme.render_data)
        self.assertEqual(len(scheme.generation_id), 64)

    def test_incompatible_schema_is_rejected(self) -> None:
        with self.assertRaises(MatugenError) as caught:
            parse_matugen_json('{"colors":{"dark":{}}}')

        self.assertEqual(caught.exception.category, "backend_incompatible")

    def test_private_image_path_is_removed_recursively_from_compiler_state(self) -> None:
        value = fixture()
        value["metadata"] = {"image": "/private/nested.png"}
        scheme = parse_matugen_json(json.dumps(value))

        self.assertNotIn("/private/wallpaper.png", json.dumps(scheme.render_data))

    def test_compiler_rejects_unknown_output_before_launch(self) -> None:
        compiler = MatugenAppearanceCompiler(Path("/bin/true"))
        request = AppearanceCompileRequest(
            AppearanceSource(AppearanceSourceKind.COLOR, "#89511E"),
            AppearanceMode.DARK,
            ("unknown",),
        )
        with self.assertRaises(AppearanceCompilerError) as caught:
            compiler.compile(request)
        self.assertEqual(caught.exception.category, "compiler_incompatible")

    @unittest.skipUnless(Path("/usr/bin/matugen").is_file(), "matugen is not installed")
    def test_palette_only_compile_is_deterministic_and_never_renders_targets(self) -> None:
        request = AppearanceCompileRequest(
            AppearanceSource(AppearanceSourceKind.COLOR, "#89511E"),
            AppearanceMode.DARK,
            (),
        )
        compiler = MatugenAppearanceCompiler()
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory) / "live"
            live.mkdir()
            marker = live / "marker"
            marker.write_text("unchanged", encoding="utf-8")
            environment = {name: str(live) for name in (
                "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
            )}
            with patch.dict("os.environ", environment, clear=False), patch.object(
                compiler, "_render", side_effect=AssertionError("target renderer must not run"),
            ):
                first = compiler.compile(request)
                second = compiler.compile(request)

            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(tuple(live.iterdir()), (marker,))

        self.assertEqual(first.outputs, ())
        self.assertEqual(first.generation_id, second.generation_id)
        self.assertTrue(dict(first.scheme)["primary"].startswith("#"))
        self.assertEqual(set(dict(first.palette)), {"primary", "secondary", "background", "foreground", "muted"})

    def test_compiler_rejects_missing_image_without_launch(self) -> None:
        compiler = MatugenAppearanceCompiler(Path("/bin/true"))
        request = AppearanceCompileRequest(
            AppearanceSource(AppearanceSourceKind.IMAGE, "/does/not/exist.png"),
            AppearanceMode.DARK,
            ("gtk4",),
        )
        with self.assertRaises(AppearanceCompilerError) as caught:
            compiler.compile(request)
        self.assertEqual(caught.exception.category, "invalid_source")

    def test_template_timeout_terminates_then_kills_process_group(self) -> None:
        class TimedOutProcess:
            pid = 4242
            returncode = None

            def __init__(self) -> None:
                self.calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls < 3:
                    raise subprocess.TimeoutExpired(["matugen"], timeout)
                return "", ""

            def poll(self):
                return None

        process = TimedOutProcess()
        scheme = parse_matugen_json(fixture())
        compiler = MatugenAppearanceCompiler(Path("/bin/true"), timeout_seconds=0.01)
        with patch("luminophore_shell.matugen.subprocess.Popen", return_value=process), patch("luminophore_shell.matugen.os.killpg") as killpg:
            with self.assertRaises(AppearanceCompilerError) as caught:
                compiler._render(scheme, AppearanceMode.DARK, ("gtk4",))

        self.assertEqual(caught.exception.category, "compile_timeout")
        self.assertEqual([call.args for call in killpg.call_args_list], [(4242, 15), (4242, 9)])

    def test_unresolved_template_output_is_rejected(self) -> None:
        class CompletedProcess:
            pid = 4242
            returncode = 0

            def communicate(self, timeout=None):
                config_path = Path(self.argv[self.argv.index("--config") + 1])
                text = config_path.read_text(encoding="utf-8")
                output = next(line.split("=", 1)[1].strip().strip('"') for line in text.splitlines() if line.startswith("output_path"))
                Path(output).write_bytes(b"{{ colors.missing }}")
                return "", ""

            def poll(self):
                return 0

        process = CompletedProcess()

        def launch(argv, **_kwargs):
            process.argv = list(argv)
            return process

        compiler = MatugenAppearanceCompiler(Path("/bin/true"))
        with patch("luminophore_shell.matugen.subprocess.Popen", side_effect=launch):
            with self.assertRaises(AppearanceCompilerError) as caught:
                compiler._render(parse_matugen_json(fixture()), AppearanceMode.DARK, ("gtk4",))
        self.assertEqual(caught.exception.category, "compiler_incompatible")

    @unittest.skipUnless(Path("/usr/bin/matugen").is_file(), "matugen is not installed")
    def test_compiler_color_is_deterministic_isolated_and_does_not_write_live_config(self) -> None:
        request = AppearanceCompileRequest(
            AppearanceSource(AppearanceSourceKind.COLOR, "#89511E"),
            AppearanceMode.DARK,
            ("gtk4",),
        )
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory) / "live"
            live.mkdir()
            marker = live / "marker"
            marker.write_text("unchanged", encoding="utf-8")
            environment = {"XDG_CONFIG_HOME": str(live), "XDG_STATE_HOME": str(live), "XDG_CACHE_HOME": str(live), "XDG_DATA_HOME": str(live)}
            with patch.dict("os.environ", environment, clear=False):
                first = MatugenAppearanceCompiler().compile(request)
                second = MatugenAppearanceCompiler().compile(request)

            self.assertEqual(first.generation_id, second.generation_id)
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({path.name for path in live.iterdir()}, {"marker"})
            self.assertEqual(set(dict(first.outputs)), {"gtk4"})
            self.assertNotIn(b"{{", b"".join(dict(first.outputs).values()))

    @unittest.skipUnless(Path("/usr/bin/matugen").is_file(), "matugen is not installed")
    def test_compiler_image_supports_light_mode_without_persisting_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-seed.png"
            Image.new("RGB", (32, 32), "#89511E").save(path)
            request = AppearanceCompileRequest(
                AppearanceSource(AppearanceSourceKind.IMAGE, str(path)),
                AppearanceMode.LIGHT,
                ("gtk3",),
            )
            result = MatugenAppearanceCompiler().compile(request)
        serialized = json.dumps(result.to_wire())
        self.assertNotIn("private-seed.png", serialized)

    @unittest.skipUnless(Path("/usr/bin/matugen").is_file(), "matugen is not installed")
    def test_installed_cli_generates_image_scheme_without_path_in_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed.png"
            Image.new("RGB", (32, 32), "#89511E").save(path)
            result = MatugenPaletteBackend().generate_image(path)

        self.assertTrue(result.palette.primary.startswith("#"))
        self.assertNotIn("image", result.scheme.render_data)


if __name__ == "__main__":
    unittest.main()
