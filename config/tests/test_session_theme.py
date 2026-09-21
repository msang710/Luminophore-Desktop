from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from luminophore_shell.hyprland import MonitorRecord, WorkspaceRef
from luminophore_shell.matugen import MatugenModeTokens, MatugenScheme
from luminophore_shell.session_theme import (
    GREETD_SESSION_COMMAND,
    LUMINOPHORE_GREETER_SESSION_PATH,
    LuminophoreGreeterRenderer,
    PlymouthInstaller,
    PlymouthRenderer,
    RetainSplashInstaller,
    SessionStackInstaller,
    SessionThemeError,
    SessionThemeGeneration,
    stage_session_theme,
    resolve_release_greeter,
    validate_rendered,
)
from luminophore_shell.state import PaletteState, PaletteStateEntry
from luminophore_shell.theme import Palette
from luminophore_shell.wallpaper_backends import WallpaperSnapshot, WallpaperSource


GENERATION = SessionThemeGeneration(
    "1234567890abcdef1234567890abcdef", "dark", "#78DCE8", "#AB9DF2",
    "#0A0D12", "#F3F7FA", "#FFB4AB",
)
WORKSPACE = WorkspaceRef(1, "1")


def fake_session_dependencies(root: Path) -> None:
    for relative in (
        "usr/bin/greetd", "usr/bin/start-hyprland", "usr/bin/uwsm", "usr/bin/hyprctl",
        "usr/bin/hyprpaper", "usr/bin/python3", "usr/bin/systemctl",
        "usr/lib/luminophore/luminophore-greeter-session",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    desktop_entry = root / "usr/share/wayland-sessions/luminophore.desktop"
    desktop_entry.parent.mkdir(parents=True, exist_ok=True)
    desktop_entry.write_text(
        "[Desktop Entry]\nName=Luminophore\nExec=/usr/bin/luminophore-session start\n",
        encoding="utf-8",
    )
    passwd = root / "etc/passwd"
    passwd.parent.mkdir(parents=True, exist_ok=True)
    passwd.write_text("testuser:x:1000:1000::/home/testuser:/bin/sh\n", encoding="utf-8")
    typelibs = root / "usr/lib/girepository-1.0"
    typelibs.mkdir(parents=True, exist_ok=True)
    for name in ("Gtk-4.0.typelib", "Gtk4LayerShell-1.0.typelib"):
        (typelibs / name).write_bytes(b"fixture")


def palette_state(provider: str = "hyprpaper") -> PaletteState:
    colors = {
        "primary": "#78DCE8",
        "secondary": "#AB9DF2",
        "surface": "#0A0D12",
        "on_surface": "#F3F7FA",
        "error": "#FFB4AB",
    }
    scheme = MatugenScheme(
        "a" * 64,
        "#78DCE8",
        {
            "dark": MatugenModeTokens("dark", colors),
            "light": MatugenModeTokens("light", colors),
        },
        {},
    )
    return PaletteState(
        1.0,
        provider,
        {"DP-2": PaletteStateEntry(Palette("#78DCE8", "#AB9DF2"), scheme)},
    )


def image(path: Path, color: str) -> None:
    Image.new("RGB", (32, 24), color).save(path)


class LuminophoreGreeterThemeTests(unittest.TestCase):
    def test_release_greeter_rejects_cross_generation_and_missing_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generation = "a" * 64
            root = Path(directory) / generation
            root.mkdir()
            entries = {}
            for name in ("greeter_compositor", "greeter_control", "greeter_shell", "greeter_session"):
                path = root / name
                path.write_text(name)
                entries[name] = {"path": name, "args": []}
            manifest = {"generation": generation, "entries": entries}
            self.assertEqual(set(resolve_release_greeter(root, manifest)), set(entries))
            manifest["generation"] = "b" * 64
            with self.assertRaisesRegex(SessionThemeError, "generation"):
                resolve_release_greeter(root, manifest)
            manifest["generation"] = generation
            entries.pop("greeter_control")
            with self.assertRaisesRegex(SessionThemeError, "component"):
                resolve_release_greeter(root, manifest)

    def test_two_monitor_render_is_first_party_minimal_and_portable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left, right = root / "left.png", root / "right.png"
            image(left, "#123456")
            image(right, "#654321")

            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", GENERATION, ("DP-2", "DP-1"), "DP-2",
                {"DP-2": left, "DP-1": right},
            )

            validate_rendered(rendered)
            theme = json.loads((rendered.root / "greeter-theme.json").read_text(encoding="utf-8"))
            self.assertEqual(theme["primary_connector"], "DP-2")
            self.assertEqual(
                theme["placement"],
                {"center_from_right": [2, 3], "center_from_top": [2, 3]},
            )
            self.assertNotIn("user", json.dumps(theme).casefold())
            compositor = (rendered.root / "greeter-settings.toml").read_text(encoding="utf-8")
            import tomllib
            data = tomllib.loads(compositor)
            self.assertEqual(data['native']['profile'], 'greeter')
            self.assertFalse(data['motion']['enabled'])
            self.assertEqual(data['compositor']['background_color'].lower(), 'ff0a0d12')
            self.assertEqual(data['native']['layer_rules'][0]['match']['namespace'], '^luminophore-shell-login$')
            self.assertNotIn('startup', data['native'])
            hyprpaper = (rendered.root / "greeter-hyprpaper.conf").read_text(encoding="utf-8")
            self.assertIn("monitor = DP-2", hyprpaper)
            self.assertIn("monitor = DP-1", hyprpaper)
            self.assertTrue((rendered.root / "python/luminophore_shell/luminophore_greeter.py").is_file())
            self.assertTrue((rendered.root / "python/luminophore_shell/bootstrap.py").is_file())
            self.assertTrue((rendered.root / "python/luminophore_shell/ui/effects.py").is_file())
            launcher = (rendered.root / "luminophore-greeter").read_text(encoding="utf-8")
            self.assertIn("os.execve", launcher)
            self.assertIn("without_preload", launcher)
            manifest = (rendered.root / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn(str(left), manifest)
            self.assertNotIn(str(right), manifest)
            self.assertNotIn("testuser", manifest)

            preview_argv = LuminophoreGreeterRenderer.preview_argv(rendered)
            self.assertEqual(preview_argv[0], "/usr/bin/python3")
            self.assertIn("--test-mode", preview_argv)
            self.assertEqual(preview_argv[-1], "failure,success")

    def test_live_stage_orders_monitors_and_materializes_solid_color(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.png"
            image(left, "#123456")
            monitors = (
                MonitorRecord(1, "DP-1", 1920, 0, 1920, 1080, WORKSPACE),
                MonitorRecord(0, "DP-2", 0, 0, 1920, 1080, WORKSPACE),
            )
            snapshot = WallpaperSnapshot(
                "hyprpaper",
                {
                    "DP-2": WallpaperSource("image", str(left)),
                    "DP-1": WallpaperSource("color", "#654321"),
                },
            )

            rendered = stage_session_theme(
                root / "stages", "dark", monitors, snapshot, palette_state(), nonce=1,
            )

            validate_rendered(rendered)
            theme = json.loads((rendered.root / "greeter-theme.json").read_text(encoding="utf-8"))
            self.assertEqual(theme["primary_connector"], "DP-2")
            self.assertEqual(len(tuple((rendered.root / "backgrounds").iterdir())), 2)
            self.assertNotIn(str(left), (rendered.root / "manifest.json").read_text(encoding="utf-8"))

    def test_stage_fails_closed_on_provider_drift_source_symlink_and_three_monitors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            link = root / "link.png"
            link.symlink_to(source)
            monitors = (MonitorRecord(0, "DP-2", 0, 0, 1920, 1080, WORKSPACE),)

            with self.assertRaisesRegex(SessionThemeError, "palette_provider_mismatch"):
                stage_session_theme(
                    root / "provider", "dark", monitors,
                    WallpaperSnapshot("awww", {"DP-2": WallpaperSource("image", str(source))}),
                    palette_state(), nonce=2,
                )
            with self.assertRaisesRegex(SessionThemeError, "unsafe_source"):
                stage_session_theme(
                    root / "symlink", "dark", monitors,
                    WallpaperSnapshot("hyprpaper", {"DP-2": WallpaperSource("image", str(link))}),
                    palette_state(), nonce=3,
                )
            paths = {name: source for name in ("A", "B", "C")}
            with self.assertRaisesRegex(SessionThemeError, "unsupported_monitor_layout"):
                LuminophoreGreeterRenderer().render(root / "three", GENERATION, tuple(paths), "A", paths)

    def test_fixed_palette_can_stage_with_independent_wallpaper_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            monitors = (MonitorRecord(0, "DP-2", 0, 0, 1920, 1080, WORKSPACE),)
            rendered = stage_session_theme(
                root / "stages", "dark", monitors,
                WallpaperSnapshot("hyprpaper", {"DP-2": WallpaperSource("image", str(source))}),
                palette_state("fixed"), nonce=4,
            )
            validate_rendered(rendered)

    def test_session_install_and_rollback_restore_exact_files_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", GENERATION, ("DP-2",), "DP-2", {"DP-2": source},
            )
            fake_session_dependencies(root)
            package_config = root / "etc/greetd/config.toml"
            package_config.parent.mkdir(parents=True)
            package_config.write_text('[default_session]\ncommand="package"\n', encoding="utf-8")
            greetd = root / "etc/greetd/greetd.conf"
            original = '[default_session]\ncommand="old"\nuser="greeter"\n'
            greetd.write_text(original, encoding="utf-8")
            os.chmod(greetd, 0o640)
            legacy_compositor = root / "etc/luminophore-shell/greeter-hyprland.conf"
            legacy_compositor.parent.mkdir(parents=True, exist_ok=True)
            legacy_compositor.write_text("legacy compositor\n", encoding="utf-8")
            os.chmod(legacy_compositor, 0o640)

            installer = SessionStackInstaller(root, "testuser")
            backup = installer.install(rendered)

            self.assertIn(GREETD_SESSION_COMMAND, greetd.read_text(encoding="utf-8"))
            self.assertEqual(greetd.stat().st_mode & 0o777, 0o600)
            self.assertEqual(package_config.read_text(encoding="utf-8"), '[default_session]\ncommand="package"\n')
            self.assertTrue((root / "etc/luminophore-shell/greeter-settings.toml").is_file())
            self.assertFalse(legacy_compositor.exists())
            self.assertTrue((root / "etc/luminophore-shell/greeter-hyprpaper.conf").is_file())
            local = root / "etc/luminophore-shell/greeter.json"
            self.assertEqual(local.stat().st_mode & 0o777, 0o640)
            self.assertEqual(json.loads(local.read_text())["login_user"], "testuser")
            self.assertEqual((root / "usr/lib/luminophore-shell/luminophore-greeter").stat().st_mode & 0o777, 0o755)
            self.assertNotIn("testuser", (backup / "rollback.json").read_text(encoding="utf-8"))

            installer.rollback(backup)

            self.assertEqual(greetd.read_text(encoding="utf-8"), original)
            self.assertEqual(greetd.stat().st_mode & 0o777, 0o640)
            self.assertEqual(legacy_compositor.read_text(encoding="utf-8"), "legacy compositor\n")
            self.assertEqual(legacy_compositor.stat().st_mode & 0o777, 0o640)
            self.assertFalse((root / "etc/luminophore-shell/greeter-settings.toml").exists())
            self.assertFalse((root / "etc/luminophore-shell/greeter-theme.json").exists())
            self.assertFalse((root / "etc/luminophore-shell/greeter.json").exists())
            self.assertFalse((root / "usr/lib/luminophore-shell/luminophore-greeter").exists())

    def test_manifest_checksum_escape_symlink_target_and_missing_dependencies_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", GENERATION, ("DP-2",), "DP-2", {"DP-2": source},
            )
            (rendered.root / "greeter-theme.json").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(SessionThemeError, "checksum_mismatch"):
                SessionStackInstaller(root, "testuser").install(rendered)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", GENERATION, ("DP-2",), "DP-2", {"DP-2": source},
            )
            manifest_path = rendered.root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["../../outside"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SessionThemeError, "invalid_manifest"):
                validate_rendered(rendered)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", GENERATION, ("DP-2",), "DP-2", {"DP-2": source},
            )
            outside = root / "outside"
            outside.mkdir()
            (root / "etc").mkdir()
            (root / "etc/luminophore-shell").symlink_to(outside, target_is_directory=True)
            fake_session_dependencies(root)
            with self.assertRaisesRegex(SessionThemeError, "unsafe_target"):
                SessionStackInstaller(root, "testuser").install(rendered)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", GENERATION, ("DP-2",), "DP-2", {"DP-2": source},
            )
            passwd = root / "etc/passwd"
            passwd.parent.mkdir(parents=True)
            passwd.write_text("testuser:x:1000:1000::/home/testuser:/bin/sh\n", encoding="utf-8")
            with self.assertRaisesRegex(SessionThemeError, "missing_dependency"):
                SessionStackInstaller(root, "testuser").install(rendered)
            self.assertFalse((root / "etc/greetd/greetd.conf").exists())

    def test_install_requires_uwsm_and_desktop_entry(self) -> None:
        for missing in (
            "usr/bin/greetd",
            "usr/bin/uwsm",
            "usr/lib/luminophore/luminophore-greeter-session",
            "usr/share/wayland-sessions/luminophore.desktop",
        ):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "wall.png"
                image(source, "#123456")
                rendered = LuminophoreGreeterRenderer().render(
                    root / "stage", GENERATION, ("DP-2",), "DP-2", {"DP-2": source},
                )
                fake_session_dependencies(root)
                (root / missing).unlink()
                with self.assertRaisesRegex(SessionThemeError, "missing_dependency"):
                    SessionStackInstaller(root, "testuser").install(rendered)

    def test_partial_session_install_failure_restores_existing_greetd_and_new_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            image(source, "#123456")
            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", GENERATION, ("DP-2",), "DP-2", {"DP-2": source},
            )
            fake_session_dependencies(root)
            greetd = root / "etc/greetd/greetd.conf"
            greetd.parent.mkdir(parents=True)
            greetd.write_text("original\n", encoding="utf-8")
            os.chmod(greetd, 0o640)
            from luminophore_shell import session_theme as module

            real_atomic_write = module._atomic_write
            injected = False

            def failing_write(path: Path, payload: bytes, mode: int = 0o644) -> None:
                nonlocal injected
                if path == greetd and not injected:
                    injected = True
                    raise OSError("injected greetd write failure")
                real_atomic_write(path, payload, mode)

            with patch("luminophore_shell.session_theme._atomic_write", side_effect=failing_write):
                with self.assertRaisesRegex(OSError, "injected greetd write failure"):
                    SessionStackInstaller(root, "testuser").install(rendered)

            self.assertEqual(greetd.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(greetd.stat().st_mode & 0o777, 0o640)
            self.assertFalse((root / "etc/luminophore-shell/greeter-theme.json").exists())
            self.assertFalse((root / "etc/luminophore-shell/greeter.json").exists())
            self.assertFalse((root / "usr/lib/luminophore-shell/luminophore-greeter").exists())


class RetainSplashTests(unittest.TestCase):
    def _unit(self, root: Path) -> Path:
        unit = root / "usr/lib/systemd/system/plymouth-quit.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\nExecStart=-/usr/bin/plymouth quit\n", encoding="utf-8")
        return unit

    def test_install_and_rollback_restore_exact_dropin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._unit(root)
            dropin = root / RetainSplashInstaller.DROPIN_PATH.removeprefix("/")
            dropin.parent.mkdir(parents=True)
            dropin.write_text("original\n", encoding="utf-8")
            os.chmod(dropin, 0o600)
            calls: list[tuple[str, ...]] = []
            runner = lambda argv: calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, "", "")
            installer = RetainSplashInstaller(root, runner)

            backup = installer.install()

            self.assertEqual(dropin.read_bytes(), RetainSplashInstaller.DROPIN)
            self.assertEqual(dropin.stat().st_mode & 0o777, 0o644)
            installer.rollback(backup)
            self.assertEqual(dropin.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(dropin.stat().st_mode & 0o777, 0o600)
            self.assertEqual(calls, [
                ("/usr/bin/systemctl", "daemon-reload"),
                ("/usr/bin/systemctl", "daemon-reload"),
            ])

    def test_failed_reload_restores_previous_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._unit(root)
            dropin = root / RetainSplashInstaller.DROPIN_PATH.removeprefix("/")
            results = iter((1, 0))
            runner = lambda argv: subprocess.CompletedProcess(argv, next(results), "", "")

            with self.assertRaisesRegex(SessionThemeError, "daemon_reload_failed"):
                RetainSplashInstaller(root, runner).install()

            self.assertFalse(dropin.exists())

    def test_unsupported_vendor_unit_fails_closed_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = self._unit(root)
            unit.write_text("[Service]\nExecStart=/custom/quit\n", encoding="utf-8")
            installer = RetainSplashInstaller(
                root, lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
            )
            with self.assertRaisesRegex(SessionThemeError, "unsupported_plymouth_quit_unit"):
                installer.install()


class PlymouthThemeTests(unittest.TestCase):
    def test_render_contains_required_callbacks_and_no_desktop_wallpaper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rendered = PlymouthRenderer().render(Path(directory) / "stage", GENERATION)
            validate_rendered(rendered)
            script = (rendered.root / "luminophore-rice.script").read_text(encoding="utf-8")
            for callback in (
                "SetRefreshFunction", "SetBootProgressFunction", "SetDisplayPasswordFunction",
                "SetDisplayQuestionFunction", "SetDisplayMessageFunction", "SetHideMessageFunction",
                "SetDisplayNormalFunction", "SetQuitFunction",
            ):
                self.assertIn(callback, script)
            joined = b"".join(path.read_bytes() for path in rendered.root.iterdir())
            self.assertNotIn(b"wallpaper", joined.lower())

    def test_rebuild_failure_restores_last_good_bytes_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rendered = PlymouthRenderer().render(root / "stage", GENERATION)
            config = root / "etc/plymouth/plymouthd.conf"
            config.parent.mkdir(parents=True)
            config.write_text("[Daemon]\nTheme=old\n", encoding="utf-8")
            os.chmod(config, 0o640)
            calls: list[tuple[str, ...]] = []

            def runner(argv):
                calls.append(tuple(argv))
                return subprocess.CompletedProcess(argv, 1 if len(calls) == 1 else 0, "", "")

            with self.assertRaisesRegex(SessionThemeError, "rebuild_failed"):
                PlymouthInstaller(root, runner).install(rendered)
            self.assertEqual(config.read_text(encoding="utf-8"), "[Daemon]\nTheme=old\n")
            self.assertEqual(config.stat().st_mode & 0o777, 0o640)
            self.assertFalse((root / "usr/share/plymouth/themes/luminophore-rice/luminophore-rice.script").exists())
            self.assertEqual(calls, [("/usr/bin/limine-mkinitcpio",), ("/usr/bin/limine-mkinitcpio",)])

    def test_successful_install_can_rollback_and_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rendered = PlymouthRenderer().render(root / "stage", GENERATION)
            config = root / "etc/plymouth/plymouthd.conf"
            config.parent.mkdir(parents=True)
            config.write_text("[Daemon]\nTheme=old\n", encoding="utf-8")
            calls: list[tuple[str, ...]] = []

            def runner(argv):
                calls.append(tuple(argv))
                return subprocess.CompletedProcess(argv, 0, "", "")

            installer = PlymouthInstaller(root, runner)
            backup = installer.install(rendered)
            self.assertIn("Theme=luminophore-rice", config.read_text(encoding="utf-8"))
            installer.rollback(backup)
            self.assertEqual(config.read_text(encoding="utf-8"), "[Daemon]\nTheme=old\n")
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
