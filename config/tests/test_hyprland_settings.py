from tests.domain_fixture import Domains
import tempfile
import unittest
from pathlib import Path
import subprocess

from luminophore_shell.hyprland_settings import (
    HyprlandPaletteTransaction,
    HyprlandSettingsError,
    HyprctlSettingsRuntime,
    SemanticPalette,
    validate_hyprland_settings,
)


class FakeRuntime:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.mismatch = False
        self.fail_restore = False
        self.apply_count = 0

    def read_options(self, names: tuple[str, ...]) -> dict[str, str]:
        result = {name: self.values[name] for name in names if name in self.values}
        if self.mismatch and self.apply_count == 1:
            first = next(iter(result))
            result[first] = "rgba(000000ff)"
        return result

    def apply_options(self, values: dict[str, str]) -> None:
        self.apply_count += 1
        if self.fail_restore and self.apply_count > 1:
            raise RuntimeError("restore failed")
        self.values.update(values)


def palette() -> SemanticPalette:
    return SemanticPalette.from_scheme("a" * 64, {
        "primary": "#112233",
        "surface_container": "#223344",
        "secondary": "#334455",
        "error": "#ff0000",
    })


class HyprlandSettingsTests(unittest.TestCase):
    def test_semantic_mapping_and_payload(self) -> None:
        result = palette()
        values = dict(result.values)
        self.assertEqual(values["general:col.active_border"], "rgba(112233ff)")
        self.assertEqual(set(values), {"general:col.active_border", "general:col.inactive_border"})
        self.assertEqual(result.generation_id, "a" * 64)

    def test_transaction_applies_file_and_live_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "palette.lua"
            desired = dict(palette().values)
            runtime = FakeRuntime({name: "rgba(000000ff)" for name in desired})
            result = HyprlandPaletteTransaction(runtime, target, service=Domains(self, runtime)).apply(palette())
            self.assertEqual(result.generation_id, "a" * 64)
            self.assertEqual(runtime.values, desired)
            self.assertFalse(target.exists())

    def test_readback_failure_rolls_back_file_and_live_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "palette.lua"
            target.write_bytes(b"old")
            desired = dict(palette().values)
            before = {name: "rgba(abcdef12)" for name in desired}
            runtime = FakeRuntime(before)
            runtime.mismatch = True
            with self.assertRaisesRegex(HyprlandSettingsError, "rolled back") as raised:
                HyprlandPaletteTransaction(runtime, target, service=Domains(self, runtime)).apply(palette())
            self.assertEqual(raised.exception.category, "apply_failed_rolled_back")
            self.assertEqual(runtime.values, before)
            self.assertEqual(target.read_bytes(), b"old")

    def test_rollback_failure_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            desired = dict(palette().values)
            runtime = FakeRuntime({name: "rgba(abcdef12)" for name in desired})
            runtime.mismatch = True
            runtime.fail_restore = True
            with self.assertRaises(HyprlandSettingsError) as raised:
                HyprlandPaletteTransaction(runtime, Path(directory) / "palette.lua", service=Domains(self, runtime)).apply(palette())
            self.assertEqual(raised.exception.category, "rollback_failed")

    def test_typed_settings_allowlist_and_boundaries(self) -> None:
        accepted = validate_hyprland_settings({
            "compositor.blur_size": 5,
            "motion.preset": "balanced",
            "compositor.active_opacity": 1.0,
        })
        self.assertEqual(accepted["compositor.blur_size"], 5)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_hyprland_settings({"window_rule.raw": "x"})
        with self.assertRaises(ValueError):
            validate_hyprland_settings({"compositor.blur_size": 0})

    def test_hyprctl_runtime_reads_documented_dotted_option_json(self) -> None:
        calls = []
        def runner(argv, timeout):
            calls.append((tuple(argv), timeout))
            return subprocess.CompletedProcess(argv, 0, '{"str":"rgba(112233FF)"}', "")
        runtime = HyprctlSettingsRuntime(runner, 2.0)
        result = runtime.read_options(("general:col.active_border",))
        self.assertEqual(result, {"general:col.active_border": "rgba(112233ff)"})
        self.assertEqual(calls[0][0], ("/usr/bin/hyprctl", "-j", "getoption", "general.col.active_border"))

    def test_hyprctl_runtime_normalizes_legacy_argb_integer(self) -> None:
        runtime = HyprctlSettingsRuntime(
            lambda argv, timeout: subprocess.CompletedProcess(argv, 0, '{"int":4279312947}', "")
        )
        # 0xff112233 (AARRGGBB) -> rgba(112233ff)
        self.assertEqual(
            runtime.read_options(("general:col.active_border",))["general:col.active_border"],
            "rgba(112233ff)",
        )

    def test_native_gradient_readback_and_no_independent_writer(self):
        runtime=HyprctlSettingsRuntime(lambda argv, timeout: subprocess.CompletedProcess(argv,0,'{"gradient":"ff112233 0deg"}',''))
        self.assertEqual(runtime.read_options(('general:col.active_border',)),{'general:col.active_border':'rgba(112233ff)'})
        self.assertFalse(hasattr(runtime,'apply_options'))

    def test_hyprctl_runtime_rejects_malformed_json_and_command_failure(self) -> None:
        runtime = HyprctlSettingsRuntime(
            lambda argv, timeout: subprocess.CompletedProcess(argv, 0, "not-json", "")
        )
        with self.assertRaises(HyprlandSettingsError) as malformed:
            runtime.read_options(("general:col.active_border",))
        self.assertEqual(malformed.exception.category, "runtime_incompatible")
        runtime = HyprctlSettingsRuntime(
            lambda argv, timeout: subprocess.CompletedProcess(argv, 1, "", "failed")
        )
        with self.assertRaises(HyprlandSettingsError) as failed:
            runtime.read_options(('general:col.active_border',))
        self.assertEqual(failed.exception.category, "runtime_unavailable")


if __name__ == "__main__":
    unittest.main()
