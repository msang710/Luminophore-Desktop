from __future__ import annotations
from tests.domain_fixture import Domains

from pathlib import Path
from dataclasses import replace
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from luminophore_shell.system_theme import (
    MatugenThemeBackend,
    SemanticTheme,
    SystemThemeError,
    SystemThemePaths,
    SystemThemePreview,
    SystemThemeTargetManager,
    _default_command_runner,
    contrast_ratio,
)
from luminophore_shell.matugen import MatugenPaletteBackend
from luminophore_shell.hyprland_settings import HyprlandPaletteTransaction, SEMANTIC_OPTION_MAP
from luminophore_shell.config import config_digest, load_config, write_config_patch
from luminophore_shell.system_theme import SystemThemeConfigSnapshot, SystemThemeController
from luminophore_shell.cursor_theme import CompiledCursorTheme


class FakeSettings:
    def __init__(self) -> None:
        self.values = {"color_scheme": "prefer-light", "gtk_theme": "adw-gtk3"}

    def snapshot(self) -> dict[str, str]:
        return dict(self.values)

    def apply(self, mode: str) -> None:
        self.values = {
            "color_scheme": f"prefer-{mode}",
            "gtk_theme": "adw-gtk3-dark" if mode == "dark" else "adw-gtk3",
        }

    def restore(self, values) -> None:
        self.values = dict(values)


class ForbiddenSettings(FakeSettings):
    def snapshot(self):
        raise AssertionError("unselected GTK settings must not be read")

    def apply(self, mode):
        raise AssertionError("unselected GTK settings must not be written")

    def restore(self, values):
        raise AssertionError("unselected GTK settings must not be restored")


class FakeHyprlandRuntime:
    def __init__(self, *, fail_apply: bool = False) -> None:
        self.values = {name: "rgba(010203ff)" for name in SEMANTIC_OPTION_MAP}
        self.fail_apply = fail_apply

    def read_options(self, names):
        return {name: self.values[name] for name in names}

    def apply_options(self, values):
        if self.fail_apply and any(value != "rgba(010203ff)" for value in values.values()):
            self.fail_apply = False
            raise RuntimeError("injected Hyprland failure")
        self.values.update(values)


class UnrestorableHyprlandRuntime(FakeHyprlandRuntime):
    def apply_options(self, values):
        raise RuntimeError("injected Hyprland apply and restore failure")


class FakeCursorTransaction:
    def __init__(self, root: Path) -> None:
        self.compiled = CompiledCursorTheme("cursor-generation", root)
        self.applied = False
        self.restored = False

    def available(self) -> bool:
        return True

    def prepare(self, tokens) -> CompiledCursorTheme:
        self.tokens = dict(tokens)
        return self.compiled

    def snapshot(self, destination: Path):
        destination.mkdir(parents=True)
        return {"fixture": True}

    def apply(self, compiled: CompiledCursorTheme) -> None:
        self.applied = compiled == self.compiled

    def verify(self, compiled: CompiledCursorTheme) -> None:
        if not self.applied or compiled != self.compiled:
            raise AssertionError("cursor was not applied")

    def restore(self, snapshot, backup_root: Path) -> None:
        self.restored = snapshot == {"fixture": True} and backup_root.is_dir()
        self.applied = False


def preview() -> SystemThemePreview:
    return SystemThemePreview(
        "preview-1",
        "digest-1",
        SemanticTheme(
            "dark",
            "DP-2",
            "#78DCE8",
            "#AB9DF2",
            {
                "primary": "#78DCE8",
                "on_primary": "#081018",
                "secondary": "#AB9DF2",
                "on_secondary": "#081018",
                "surface": "#0A0D12",
                "on_surface": "#F3F7FA",
                "surface_container": "#151A22",
                "error": "#FFB4AB",
                "on_error": "#690005",
                "outline": "#89939E",
            },
            "a" * 64,
        ),
        {
            "gtk3": b"gtk3-new\n",
            "gtk4": b"gtk4-new\n",
            "qt": b"qt-new\n",
            "kde": b"[General]\nColorScheme=Matugen\nName=Matugen\n",
            "kitty": b"background #101010\n",
            "alacritty": b"[colors.primary]\nbackground = '#101010'\n",
            "btop": b'theme[main_bg]="#101010"\n',
            "ghostty": b"background = #101010\n",
        },
        hyprland_settings={
            "primary": "#78DCE8",
            "surface_container": "#151A22",
            "secondary": "#AB9DF2",
            "error": "#FFB4AB",
        },
    )


class SystemThemeBackendTests(unittest.TestCase):
    def test_contrast_ratio_orders_readable_pair(self) -> None:
        self.assertGreater(contrast_ratio("#F3F7FA", "#0A0D12"), 10)
        self.assertLess(contrast_ratio("#101010", "#111111"), 1.1)

    @unittest.skipUnless(Path("/usr/bin/matugen").is_file(), "matugen is not installed")
    def test_matugen_backend_renders_all_application_outputs_in_isolation(self) -> None:
        scheme = MatugenPaletteBackend().generate_color("#89511E").scheme
        result = MatugenThemeBackend(timeout_seconds=10).preview(
            scheme.modes["dark"].colors["primary"],
            scheme.modes["dark"].colors["secondary"],
            "dark",
            "DP-2",
            "config-digest",
            scheme,
        )

        self.assertEqual(set(result.outputs), {
            "gtk3", "gtk4", "qt", "kde", "kitty", "alacritty", "btop", "ghostty",
        })
        self.assertIn(b"@define-color", result.outputs["gtk4"])
        self.assertNotIn(b"{{", b"".join(result.outputs.values()))
        self.assertIn("colors", tomllib.loads(result.outputs["alacritty"].decode()))
        self.assertIn("theme[main_bg]", result.outputs["btop"].decode())
        self.assertEqual(result.semantic.generation_id, scheme.generation_id)
        self.assertEqual(result.hyprland_settings, {
            "primary": scheme.modes["dark"].colors["primary"],
            "surface_container": scheme.modes["dark"].colors["surface_container"],
            "secondary": scheme.modes["dark"].colors["secondary"],
            "error": scheme.modes["dark"].colors["error"],
        })


class SystemThemeCommandTests(unittest.TestCase):
    @patch("luminophore_shell.system_theme.subprocess.run")
    def test_plasma_activation_uses_display_independent_qt_platform(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        _default_command_runner(("/usr/bin/plasma-apply-colorscheme", "matugen"))

        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["QT_QPA_PLATFORM"], "offscreen")
        self.assertEqual(environment["QT_QPA_PLATFORMTHEME"], "")


class SystemThemeTransactionTests(unittest.TestCase):
    def _fixture(self, directory: str, *, fail_apply: bool = False):
        root = Path(directory)
        paths = SystemThemePaths(root / "config", root / "data", root / "state")
        originals: dict[str, bytes] = {}
        for name, path in paths.targets().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == "kdeglobals":
                payload = b"[General]\nColorScheme=Old\nName=old\n"
            elif name == "kitty_config":
                payload = b"include themes/matugen.conf\n"
            elif name == "alacritty_config":
                payload = b'[general]\nimport = ["~/.config/alacritty/themes/matugen.toml"]\n'
            elif name == "btop_config":
                payload = b'color_theme = "matugen"\n'
            elif name == "ghostty_config":
                payload = b"theme = old\n"
            elif name.endswith("_config"):
                payload = b"[Appearance]\ncustom_palette=false\n"
            else:
                payload = f"old-{name}\n".encode()
            path.write_bytes(payload)
            originals[name] = payload
        settings = FakeSettings()

        def runner(argv):
            scheme = argv[-1]
            if scheme.casefold() == "matugen" and fail_apply:
                raise SystemThemeError("apply_failed", "injected KDE activation failure")
            if Path(argv[0]).name == "plasma-apply-colorscheme":
                kdeglobals = paths.targets()["kdeglobals"]
                if scheme.casefold() == "matugen":
                    kdeglobals.write_bytes(paths.targets()["kde_palette"].read_bytes())
                elif scheme == "Old":
                    kdeglobals.write_bytes(originals["kdeglobals"])
            return subprocess.CompletedProcess(argv, 0, "", "")

        manager = SystemThemeTargetManager(paths, settings, runner)
        return manager, paths, settings, originals

    def test_apply_records_backup_detects_drift_and_rolls_back_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, settings, originals = self._fixture(directory)

            generation = manager.apply(preview())

            self.assertTrue(generation)
            self.assertTrue(manager.backup_available())
            self.assertFalse(manager.drifted())
            self.assertEqual(settings.values["color_scheme"], "prefer-dark")
            self.assertEqual(paths.targets()["gtk4_palette"].read_bytes(), b"gtk4-new\n")
            self.assertIn("Name=matugen", paths.targets()["kdeglobals"].read_text())
            paths.targets()["ghostty_config"].write_bytes(b"external change")
            self.assertTrue(manager.drifted())

            manager.rollback()

            self.assertFalse(manager.backup_available())
            self.assertEqual(settings.values, {"color_scheme": "prefer-light", "gtk_theme": "adw-gtk3"})
            for name, payload in originals.items():
                self.assertEqual(paths.targets()[name].read_bytes(), payload)

    def test_partial_apply_failure_automatically_restores_every_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, settings, originals = self._fixture(directory, fail_apply=True)

            with self.assertRaisesRegex(SystemThemeError, "이전 색상으로 복구") as caught:
                manager.apply(preview())

            self.assertEqual(caught.exception.category, "apply_failed_rolled_back")
            self.assertEqual(settings.values, {"color_scheme": "prefer-light", "gtk_theme": "adw-gtk3"})
            for name, payload in originals.items():
                self.assertEqual(paths.targets()[name].read_bytes(), payload)

    def test_missing_optional_app_is_skipped_without_blocking_core_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, _settings, _originals = self._fixture(directory)
            paths.targets()["kitty_config"].unlink()
            paths.targets()["kitty_palette"].unlink()

            manager.apply(preview())

            self.assertFalse(manager.availability()["kitty"])
            self.assertFalse(paths.targets()["kitty_palette"].exists())
            self.assertEqual(paths.targets()["gtk4_palette"].read_bytes(), b"gtk4-new\n")

    def test_capability_drift_after_preview_aborts_before_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, _settings, _originals = self._fixture(directory)
            bound = manager.bind_preview(preview())
            paths.targets()["kitty_config"].unlink()

            with self.assertRaises(SystemThemeError) as caught:
                manager.apply(bound)

            self.assertEqual(caught.exception.category, "stale_preview")
            self.assertFalse(manager.backup_available())
            self.assertFalse(paths.targets()["kitty_config"].exists())

    def test_only_installed_targets_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, _settings, _originals = self._fixture(directory)
            targets = paths.targets()
            for target in ("qt5", "kde", "kitty", "alacritty", "btop", "ghostty"):
                for name in manager.TARGET_FILES[target]:
                    targets[name].unlink(missing_ok=True)

            bound = manager.bind_preview(preview())
            manager.apply(bound)

            self.assertEqual(set(bound.target_ids), {"gtk3", "gtk4", "qt6"})
            for target in ("qt5", "kde", "kitty", "alacritty", "btop", "ghostty"):
                for name in manager.TARGET_FILES[target]:
                    self.assertFalse(targets[name].exists(), name)

    def test_qt_only_transaction_never_reads_writes_or_restores_unselected_gtk_kde(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SystemThemePaths(
                root / "config", root / "data", root / "state",
                root / "missing-plasma", root / "missing-gsettings",
            )
            qt6 = paths.targets()["qt6_config"]
            qt6.parent.mkdir(parents=True)
            qt6.write_text("[Appearance]\ncustom_palette=false\n", encoding="utf-8")
            manager = SystemThemeTargetManager(paths, ForbiddenSettings(), lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
            bound = manager.bind_preview(preview())
            self.assertEqual(bound.target_ids, ("qt6",))

            manager.apply(bound)
            manager.rollback()

            self.assertEqual(qt6.read_text(), "[Appearance]\ncustom_palette=false\n")
            self.assertFalse(paths.targets()["gtk3_palette"].exists())
            self.assertFalse(paths.targets()["kde_palette"].exists())

    def test_each_target_write_failure_restores_exact_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, settings, originals = self._fixture(directory)
            bound = manager.bind_preview(preview())
            original_write = manager._write_payload
            failed_path = paths.targets()["ghostty_palette"]

            def failing_write(path, payload):
                if path == failed_path:
                    raise OSError("injected target write failure")
                original_write(path, payload)

            manager._write_payload = failing_write  # type: ignore[method-assign]
            with self.assertRaises(SystemThemeError) as caught:
                manager.apply(bound)

            self.assertEqual(caught.exception.category, "apply_failed_rolled_back")
            self.assertEqual(settings.values, {"color_scheme": "prefer-light", "gtk_theme": "adw-gtk3"})
            for name, payload in originals.items():
                self.assertEqual(paths.targets()[name].read_bytes(), payload, name)

    def test_application_and_hyprland_apply_and_manual_rollback_as_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, settings, originals = self._fixture(directory)
            runtime = FakeHyprlandRuntime()
            palette_path = Path(directory) / "luminophore_semantic_palette.lua"
            palette_path.write_bytes(b"old-hyprland\n")
            old_live = dict(runtime.values)
            manager.hyprland = HyprlandPaletteTransaction(runtime, palette_path, service=Domains(self, runtime))

            manager.apply(manager.bind_preview(preview()))

            self.assertNotEqual(runtime.values, old_live)
            self.assertEqual(palette_path.read_bytes(), b"old-hyprland\n")
            manager.rollback()
            self.assertEqual(runtime.values, old_live)
            self.assertEqual(palette_path.read_bytes(), b"old-hyprland\n")
            self.assertEqual(settings.values, {"color_scheme": "prefer-light", "gtk_theme": "adw-gtk3"})
            for name, payload in originals.items():
                self.assertEqual(paths.targets()[name].read_bytes(), payload, name)

    def test_cursor_is_bound_applied_verified_and_rolled_back_in_same_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, _paths, _settings, _originals = self._fixture(directory)
            cursor = FakeCursorTransaction(Path(directory) / "compiled")
            manager.cursor = cursor

            bound = manager.bind_preview(preview())
            self.assertIn("cursor", bound.target_ids)
            self.assertEqual(bound.cursor_generation, "cursor-generation")
            self.assertEqual(cursor.tokens["primary"], "#78DCE8")

            manager.apply(bound)
            self.assertTrue(cursor.applied)
            manager.rollback()
            self.assertTrue(cursor.restored)
            self.assertFalse(cursor.applied)

    def test_hyprland_failure_restores_application_and_hyprland_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, settings, originals = self._fixture(directory)
            runtime = FakeHyprlandRuntime(fail_apply=True)
            palette_path = Path(directory) / "luminophore_semantic_palette.lua"
            palette_path.write_bytes(b"old-hyprland\n")
            old_live = dict(runtime.values)
            manager.hyprland = HyprlandPaletteTransaction(runtime, palette_path, service=Domains(self, runtime))

            with self.assertRaises(SystemThemeError) as caught:
                manager.apply(manager.bind_preview(preview()))

            self.assertEqual(caught.exception.category, "apply_failed_rolled_back")
            self.assertEqual(runtime.values, old_live)
            self.assertEqual(palette_path.read_bytes(), b"old-hyprland\n")
            self.assertEqual(settings.values, {"color_scheme": "prefer-light", "gtk_theme": "adw-gtk3"})
            for name, payload in originals.items():
                self.assertEqual(paths.targets()[name].read_bytes(), payload, name)

    def test_application_failure_preserves_hyprland_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, _paths, _settings, _originals = self._fixture(directory, fail_apply=True)
            runtime = FakeHyprlandRuntime()
            palette_path = Path(directory) / "luminophore_semantic_palette.lua"
            palette_path.write_bytes(b"old-hyprland\n")
            old_live = dict(runtime.values)
            manager.hyprland = HyprlandPaletteTransaction(runtime, palette_path, service=Domains(self, runtime))

            with self.assertRaises(SystemThemeError) as caught:
                manager.apply(manager.bind_preview(preview()))

            self.assertEqual(caught.exception.category, "apply_failed_rolled_back")
            self.assertEqual(runtime.values, old_live)
            self.assertEqual(palette_path.read_bytes(), b"old-hyprland\n")

    def test_hyprland_apply_and_restore_failure_is_rollback_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, _paths, _settings, _originals = self._fixture(directory)
            runtime = UnrestorableHyprlandRuntime()
            palette_path = Path(directory) / "luminophore_semantic_palette.lua"
            palette_path.write_bytes(b"old-hyprland\n")
            manager.hyprland = HyprlandPaletteTransaction(runtime, palette_path, service=Domains(self, runtime))

            with self.assertRaises(SystemThemeError) as caught:
                manager.apply(manager.bind_preview(preview()))

            self.assertEqual(caught.exception.category, "rollback_failed")

    def test_first_apply_migrates_unique_existing_theme_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, _settings, _originals = self._fixture(directory)
            targets = paths.targets()
            targets["gtk3_entry"].write_text('@import url("legacy.css");\n', encoding="utf-8")
            targets["gtk4_entry"].write_text("@import url('legacy.css');\n", encoding="utf-8")
            targets["kitty_config"].write_text("include themes/legacy.conf\n", encoding="utf-8")
            targets["alacritty_config"].write_text(
                '[general]\nimport = ["~/.config/alacritty/themes/legacy.toml"]\n',
                encoding="utf-8",
            )
            targets["btop_config"].write_text('color_theme = "legacy"\n', encoding="utf-8")
            targets["ghostty_config"].write_text("theme = legacy\n", encoding="utf-8")

            manager.apply(preview())

            self.assertEqual(targets["gtk3_entry"].read_text(), '@import url("matugen.css");\n')
            self.assertEqual(targets["gtk4_entry"].read_text(), '@import url("matugen.css");\n')
            self.assertEqual(targets["kitty_config"].read_text(), "include themes/matugen.conf\n")
            self.assertIn("themes/matugen.toml", targets["alacritty_config"].read_text())
            self.assertEqual(targets["btop_config"].read_text(), 'color_theme = "matugen"\n')
            self.assertEqual(targets["ghostty_config"].read_text(), "theme = matugen\n")

    def test_restart_manager_uses_generation_manifest_to_restore_config_bytes_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager, paths, settings, _originals = self._fixture(directory)
            config_path = Path(directory) / "shell-config.toml"
            source = Path(__file__).parents[1] / "luminophore_shell/config.toml"
            config_path.write_bytes(source.read_bytes())
            before_payload = config_path.read_bytes()
            before = load_config(config_path)
            after = write_config_patch(config_path, {"system_theme.mode": "light"}, config_digest(config_path))
            snapshot = SystemThemeConfigSnapshot(before_payload, config_path.stat().st_mode & 0o777, before, after)
            adopted = []
            manager.configure_config_transaction(config_path, lambda old, new: adopted.append((old.system_theme.mode, new.system_theme.mode)))

            manager.apply(manager.bind_preview(preview()), snapshot)
            self.assertEqual(load_config(config_path).system_theme.mode, "light")

            restarted = SystemThemeTargetManager(paths, settings, manager.command_runner)
            restarted.configure_config_transaction(config_path, lambda old, new: adopted.append((old.system_theme.mode, new.system_theme.mode)))
            restarted.rollback()

            self.assertEqual(config_path.read_bytes(), before_payload)
            self.assertEqual(load_config(config_path).system_theme.mode, "dark")
            self.assertEqual(adopted, [("light", "dark")])
            self.assertFalse(restarted.backup_available())


class FakeControllerTargets:
    def __init__(self, *, fail_apply: bool = False, fail_rollback: bool = False) -> None:
        self.fail_apply = fail_apply
        self.fail_rollback = fail_rollback
        self.applied = False
        self.config_path = None
        self.config_saved = None

    def configure_config_transaction(self, path, saved):
        self.config_path = path
        self.config_saved = saved

    def backup_available(self):
        return self.applied

    def drifted(self):
        return False

    def apply(self, _preview, config_snapshot=None):
        if self.fail_apply:
            if config_snapshot is not None:
                Path(self.config_path).write_bytes(config_snapshot.payload)
                self.config_saved(config_snapshot.after, config_snapshot.before)
            raise SystemThemeError("apply_failed_rolled_back", "injected target failure")
        self.applied = True
        self.config_snapshot = config_snapshot

    def rollback(self):
        if self.fail_rollback:
            raise SystemThemeError("rollback_failed", "injected target rollback failure")
        self.applied = False
        if getattr(self, "config_snapshot", None) is not None:
            Path(self.config_path).write_bytes(self.config_snapshot.payload)
            self.config_saved(self.config_snapshot.after, self.config_snapshot.before)


class FakeControllerBackend:
    def shutdown(self):
        pass


class SystemThemeControllerOuterTransactionTests(unittest.TestCase):
    def _fixture(self, directory: str, targets: FakeControllerTargets, callback=None):
        path = Path(directory) / "config.toml"
        source = Path(__file__).parents[1] / "luminophore_shell/config.toml"
        path.write_bytes(source.read_bytes())
        before = path.read_bytes()
        calls = []

        def saved(old, new):
            calls.append((old.system_theme.mode, new.system_theme.mode))
            if callback:
                callback(len(calls))

        controller = SystemThemeController(
            path, lambda: None, lambda _state: None, saved,
            backend=FakeControllerBackend(), targets=targets,  # type: ignore[arg-type]
        )
        item = replace(preview(), config_digest=config_digest(path))
        return controller, path, before, calls, item

    def test_target_apply_failure_restores_config_bytes_and_runtime_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller, path, before, calls, item = self._fixture(directory, FakeControllerTargets(fail_apply=True))
            item = replace(item, semantic=replace(item.semantic, mode="light"))
            controller._apply_worker(item)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(load_config(path).system_theme.mode, "dark")
            self.assertEqual(calls, [("dark", "light"), ("light", "dark")])
            self.assertEqual(controller.state.error_category, "apply_failed_rolled_back")

    def test_success_then_manual_rollback_restores_config_bytes_and_runtime_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            targets = FakeControllerTargets()
            controller, path, before, calls, item = self._fixture(directory, targets)
            item = replace(item, semantic=replace(item.semantic, mode="light"))
            controller._apply_worker(item)
            self.assertNotEqual(path.read_bytes(), before)
            self.assertEqual(load_config(path).system_theme.mode, "light")
            controller._rollback_worker()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(load_config(path).system_theme.mode, "dark")
            self.assertEqual(calls, [("dark", "light"), ("light", "dark")])

    def test_runtime_adoption_failure_restores_exact_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def fail_first(call_number):
                if call_number == 1:
                    raise RuntimeError("injected runtime adoption failure")

            controller, path, before, calls, item = self._fixture(directory, FakeControllerTargets(), fail_first)
            item = replace(item, semantic=replace(item.semantic, mode="light"))
            controller._apply_worker(item)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(calls, [("dark", "light"), ("light", "dark")])
            self.assertEqual(controller.state.error_category, "apply_failed")

    def test_manual_target_rollback_failure_still_restores_config_and_reports_rollback_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            targets = FakeControllerTargets(fail_rollback=True)
            controller, path, before, _calls, item = self._fixture(directory, targets)
            item = replace(item, semantic=replace(item.semantic, mode="light"))
            controller._apply_worker(item)
            controller._rollback_worker()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(controller.state.error_category, "rollback_failed")


if __name__ == "__main__":
    unittest.main()
