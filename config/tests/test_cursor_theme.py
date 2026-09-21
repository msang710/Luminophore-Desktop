from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.cursor_theme import (
    CURSOR_SIZE,
    SOURCE_ROOT,
    THEME_NAME,
    CompiledCursorTheme,
    CursorThemeCompiler,
    CursorThemeError,
    CursorThemePalette,
    CursorThemePaths,
    CursorThemeTransaction,
    _file_digests,
    source_inventory,
    verify_source_integrity,
)


TOKENS = {
    "primary": "#78DCE8",
    "surface": "#0A0D12",
    "secondary": "#AB9DF2",
    "tertiary": "#FFD866",
    "error": "#FF6188",
    "on_surface_variant": "#89939E",
}


def fixture(root: Path) -> Path:
    source = root / "source"
    shape = source / "hyprcursors/left_ptr"
    shape.mkdir(parents=True)
    (source / "manifest.hl").write_text(
        "name = Bibata-Modern-Ice\ndescription = fixture\nversion = 1\ncursors_directory = hyprcursors\n"
    )
    (source / "index.theme").write_text("[Icon Theme]\nName=Bibata-Modern-Ice\n")
    (shape / "meta.hl").write_text(
        "resize_algorithm = none\nhotspot_x = 0.25\nhotspot_y = 0.50\n\n"
        "define_size = 0, left_ptr.svg\n\ndefine_override = default\n"
    )
    (shape / "left_ptr.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path fill="#FFFFFF" stroke="#000000"/></svg>'
    )
    return source


class CursorSourceTests(unittest.TestCase):
    def test_bundled_source_preserves_complete_installed_inventory(self) -> None:
        shapes = source_inventory(SOURCE_ROOT)
        names = {shape.name for shape in shapes}
        self.assertEqual(len(shapes), 56)
        self.assertIn("left_ptr", names)
        self.assertEqual(len(next(shape for shape in shapes if shape.name == "wait").frames), 54)
        self.assertEqual(len(next(shape for shape in shapes if shape.name == "left_ptr_watch").frames), 54)

    def test_palette_requires_semantic_tokens(self) -> None:
        palette = CursorThemePalette.from_tokens(TOKENS)
        self.assertEqual(palette.primary, "#78DCE8")
        with self.assertRaises(CursorThemeError) as caught:
            CursorThemePalette.from_tokens({**TOKENS, "primary": "bad"})
        self.assertEqual(caught.exception.category, "validation")

    def test_bundled_source_integrity_manifest_passes(self) -> None:
        verify_source_integrity(SOURCE_ROOT)


class CursorCompilerTests(unittest.TestCase):
    def test_compile_builds_both_formats_and_reuses_verified_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = fixture(root)
            tools = root / "tools"
            tools.mkdir()
            executables = {name: tools / name for name in ("hyprcursor-util", "rsvg-convert", "xcursorgen")}
            for path in executables.values():
                path.write_text("fixture")
            calls: list[tuple[str, ...]] = []

            def runner(argv):
                command = tuple(str(value) for value in argv)
                calls.append(command)
                if Path(command[0]).name == "hyprcursor-util":
                    output = Path(command[command.index("--output") + 1]) / f"theme_{THEME_NAME}/hyprcursors"
                    output.mkdir(parents=True)
                    (output / "left_ptr.hlc").write_bytes(b"hypr")
                elif Path(command[0]).name == "rsvg-convert":
                    Path(command[command.index("-o") + 1]).write_bytes(b"png")
                else:
                    Path(command[-1]).write_bytes(b"xcursor")
                return subprocess.CompletedProcess(command, 0, "", "")

            compiler = CursorThemeCompiler(
                source,
                root / "cache",
                executables["hyprcursor-util"],
                executables["rsvg-convert"],
                executables["xcursorgen"],
                runner,
            )
            first = compiler.compile(TOKENS)
            call_count = len(calls)
            second = compiler.compile(TOKENS)

            self.assertEqual(first.generation_id, second.generation_id)
            self.assertEqual(first.theme_name, THEME_NAME)
            self.assertEqual(first.size, CURSOR_SIZE)
            self.assertTrue((first.root / "hyprcursors/left_ptr.hlc").is_file())
            self.assertTrue((first.root / "cursors/left_ptr").is_file())
            self.assertTrue((first.root / "cursors/default").is_symlink())
            self.assertEqual(len(calls), call_count)
            self.assertEqual(json.loads((first.root.parent / "digests.json").read_text()), dict(first.digests))

    def test_missing_xcursor_compiler_fails_before_writing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compiler = CursorThemeCompiler(
                fixture(root), root / "cache", root / "hypr", root / "rsvg", root / "missing"
            )
            with self.assertRaises(CursorThemeError) as caught:
                compiler.compile(TOKENS)
            self.assertEqual(caught.exception.category, "compiler_missing")
            self.assertFalse((root / "cache").exists())


class CursorTransactionTests(unittest.TestCase):
    def test_apply_verify_and_restore_preserve_directory_selectors_and_live_theme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = CursorThemePaths(root / "config", root / "data", root / "missing-gsettings")
            active = paths.active_theme
            active.mkdir(parents=True)
            (active / "old").write_bytes(b"old-theme")
            original_selectors: dict[Path, bytes] = {}
            for path, payload in zip(paths.selectors(), (
                b'export HYPRCURSOR_THEME="Old"\nexport HYPRCURSOR_SIZE=24\n',
                b"[Settings]\ngtk-cursor-theme-name=Old\ngtk-cursor-theme-size=24\n",
                b'Gtk/CursorThemeName "Old"\nGtk/CursorThemeSize 24\n',
            )):
                path.parent.mkdir(parents=True)
                path.write_bytes(payload)
                original_selectors[path] = payload
            compiled_root = root / "compiled" / THEME_NAME
            (compiled_root / "hyprcursors").mkdir(parents=True)
            (compiled_root / "cursors").mkdir()
            (compiled_root / "manifest.hl").write_bytes(b"new-theme")
            (compiled_root / "hyprcursors/left_ptr.hlc").write_bytes(b"hypr")
            (compiled_root / "cursors/left_ptr").write_bytes(b"x11")
            compiled = CompiledCursorTheme("generation", compiled_root, digests=_file_digests(compiled_root))
            calls: list[tuple[str, ...]] = []

            def runner(argv):
                calls.append(tuple(str(value) for value in argv))
                return subprocess.CompletedProcess(argv, 0, "ok", "")

            transaction = CursorThemeTransaction(paths=paths, runner=runner)
            backup = root / "backup"
            with patch.dict("os.environ", {"HYPRCURSOR_THEME": "Old", "HYPRCURSOR_SIZE": "24"}):
                snapshot = transaction.snapshot(backup / "cursor")
            transaction.apply(compiled)
            transaction.verify(compiled)
            self.assertEqual((active / "manifest.hl").read_bytes(), b"new-theme")
            self.assertTrue(all(THEME_NAME in path.read_text() for path in paths.selectors()))

            transaction.restore(snapshot, backup)
            self.assertEqual((active / "old").read_bytes(), b"old-theme")
            self.assertEqual({path: path.read_bytes() for path in paths.selectors()}, original_selectors)
            self.assertEqual(calls[-1][-2:], ("Old", "24"))


if __name__ == "__main__":
    unittest.main()
