import unittest

from luminophore_shell.appearance_types import (
    AppearanceCompileRequest,
    AppearanceMode,
    AppearanceSource,
    AppearanceSourceKind,
    CompiledAppearance,
)


class AppearanceTypesTests(unittest.TestCase):
    def test_palette_only_compile_request_allows_empty_outputs(self) -> None:
        request = AppearanceCompileRequest(
            AppearanceSource(AppearanceSourceKind.COLOR, "#112233"),
            AppearanceMode.DARK,
            (),
        )
        self.assertEqual(request.required_outputs, ())

    def test_compiled_appearance_is_deterministic_and_round_trips(self) -> None:
        first = CompiledAppearance.build(
            {"surface": "#010203", "primary": "#AABBCC"},
            {"secondary": "#445566", "primary": "#AABBCC"},
            {"gtk4": b"gtk", "hyprland": b"lua"},
        )
        second = CompiledAppearance.build(
            {"primary": "#AABBCC", "surface": "#010203"},
            {"primary": "#AABBCC", "secondary": "#445566"},
            {"hyprland": b"lua", "gtk4": b"gtk"},
        )
        self.assertEqual(first, second)
        self.assertEqual(CompiledAppearance.from_wire(first.to_wire()), first)

    def test_generation_tampering_is_rejected(self) -> None:
        compiled = CompiledAppearance.build({"primary": "#fff"}, {"primary": "#fff"}, {"gtk4": b"x"})
        payload = compiled.to_wire()
        payload["generation_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "generation mismatch"):
            CompiledAppearance.from_wire(payload)

    def test_unknown_wire_field_and_version_fail_closed(self) -> None:
        compiled = CompiledAppearance.build({"primary": "#fff"}, {"primary": "#fff"}, {"gtk4": b"x"})
        payload = compiled.to_wire()
        payload["extra"] = True
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            CompiledAppearance.from_wire(payload)
        payload = compiled.to_wire()
        payload["version"] = 99
        with self.assertRaisesRegex(ValueError, "unsupported"):
            CompiledAppearance.from_wire(payload)

    def test_compile_request_requires_canonical_outputs(self) -> None:
        source = AppearanceSource(AppearanceSourceKind.COLOR, "#AABBCC")
        request = AppearanceCompileRequest(source, AppearanceMode.DARK, ("gtk4", "hyprland"))
        self.assertEqual(request.required_outputs, ("gtk4", "hyprland"))
        with self.assertRaises(ValueError):
            AppearanceCompileRequest(source, AppearanceMode.DARK, ("hyprland", "gtk4"))


if __name__ == "__main__":
    unittest.main()
