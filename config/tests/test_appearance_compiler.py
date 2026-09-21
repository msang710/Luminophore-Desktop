from __future__ import annotations

import unittest

from luminophore_shell.appearance_compiler import AppearanceCompiler, AppearanceCompilerError
from luminophore_shell.appearance_types import AppearanceCompileRequest, CompiledAppearance


class AppearanceCompilerContractTests(unittest.TestCase):
    def test_compiler_protocol_and_error_category_are_stable(self) -> None:
        self.assertTrue(hasattr(AppearanceCompiler, "compile"))
        self.assertTrue(hasattr(AppearanceCompiler, "shutdown"))
        error = AppearanceCompilerError("compiler_missing", "missing")
        self.assertEqual(error.category, "compiler_missing")
        self.assertEqual(str(error), "missing")

    def test_compiled_result_generation_is_deterministic(self) -> None:
        first = CompiledAppearance.build(
            {"surface": "#101010", "primary": "#AABBCC"},
            {"background": "#101010"},
            {"gtk4": b"payload", "hyprland": b"lua"},
        )
        second = CompiledAppearance.build(
            {"primary": "#AABBCC", "surface": "#101010"},
            {"background": "#101010"},
            {"hyprland": b"lua", "gtk4": b"payload"},
        )
        changed = CompiledAppearance.build(
            dict(first.scheme), dict(first.palette), {"gtk4": b"changed", "hyprland": b"lua"},
        )
        self.assertEqual(first.generation_id, second.generation_id)
        self.assertNotEqual(first.generation_id, changed.generation_id)

    def test_palette_only_compiled_result_has_deterministic_empty_outputs(self) -> None:
        first = CompiledAppearance.build({"primary": "#AABBCC"}, {"primary": "#AABBCC"}, {})
        second = CompiledAppearance.build({"primary": "#AABBCC"}, {"primary": "#AABBCC"}, {})
        self.assertEqual(first.outputs, ())
        self.assertEqual(first.generation_id, second.generation_id)


if __name__ == "__main__":
    unittest.main()
