from __future__ import annotations

from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.session_preflight import (
    _greetd_check,
    collect_checks,
    command_contract,
    exit_code,
    safe_run,
)


class SessionPreflightTests(unittest.TestCase):
    def test_command_contract_comes_from_desktop_capability_manifest(self) -> None:
        required, optional = command_contract()
        self.assertIn("hyprpicker", required)
        self.assertIn("xdg-user-dir", required)
        self.assertIn("fd", required)
        self.assertIn("flatpak", optional)
        self.assertIn("awww", optional)
        self.assertNotIn("start-hyprland", required)
        self.assertNotIn("hyprctl", required)

    def test_invalid_capability_manifest_is_a_required_preflight_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest = Path(raw) / "capabilities.json"
            manifest.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"LUMINOPHORE_CAPABILITY_MANIFEST": str(manifest)}):
                results = collect_checks(
                    root=Path("/nonexistent"),
                    which=lambda _name: None,
                    runner=lambda argv: subprocess.CompletedProcess(argv, 1, stdout="", stderr=""),
                )
        item = next(row for row in results if row.name == "capability-manifest")
        self.assertEqual(item.status, "MISSING")
        self.assertEqual(exit_code(results), 1)

    def test_fixture_reports_greetd_recovery_firmware_and_ddc_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "etc/greetd").mkdir(parents=True)
            (root / "etc/greetd/config.toml").write_text(
                'command = "/usr/bin/legacy-greeter-session"\n', encoding="utf-8"
            )
            (root / "etc/passwd").write_text("user:x:1000:1000::/home/user:/bin/sh\n", encoding="utf-8")
            (root / "etc/shells").write_text("/bin/sh\n", encoding="utf-8")
            (root / "usr/share/dbus-1/system-services").mkdir(parents=True)
            (root / "usr/share/dbus-1/system-services/org.freedesktop.GeoClue2.service").write_text("", encoding="utf-8")
            (root / "usr/lib/polkit-gnome").mkdir(parents=True)
            (root / "usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1").write_text("", encoding="utf-8")
            (root / "usr/lib/girepository-1.0").mkdir(parents=True)
            (root / "usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib").write_bytes(b"fixture")
            required, _optional = command_contract()
            commands = {name: f"/usr/bin/{name}" for name in required}

            def runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                self.assertEqual(argv, ("/usr/bin/systemctl", "--help"))
                return subprocess.CompletedProcess(argv, 0, stdout="--firmware-setup", stderr="")

            results = collect_checks(root=root, which=commands.get, runner=runner)
            indexed = {item.name: item for item in results}
            self.assertEqual(indexed["greetd-config"].status, "WARN")
            self.assertEqual(indexed["tty-recovery-contract"].status, "PASS")
            self.assertEqual(indexed["firmware-setup-capability"].status, "PASS")
            self.assertEqual(indexed["ddc-live"].status, "NOT_RUN")
            self.assertEqual(exit_code(results), 0)

    def test_greetd_preflight_prefers_luminophore_owned_config_and_requires_compositor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "etc/greetd").mkdir(parents=True)
            (root / "etc/greetd/config.toml").write_text('command = "old"\n', encoding="utf-8")
            (root / "etc/greetd/greetd.conf").write_text(
                'command = "/usr/bin/start-hyprland -- -c /etc/luminophore-shell/greeter-hyprland.lua"\n',
                encoding="utf-8",
            )
            (root / "etc/passwd").write_text("", encoding="utf-8")
            (root / "etc/shells").write_text("", encoding="utf-8")
            (root / "usr/lib/girepository-1.0").mkdir(parents=True)
            (root / "usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib").write_bytes(b"fixture")
            required, _optional = command_contract()
            commands = {name: f"/usr/bin/{name}" for name in required}
            results = collect_checks(
                root=root,
                which=commands.get,
                runner=lambda argv: subprocess.CompletedProcess(argv, 0, stdout="--firmware-setup", stderr=""),
            )
            indexed = {item.name: item for item in results}
            self.assertEqual(indexed["greetd-config"].status, "PASS")
            self.assertIn("greetd.conf", indexed["greetd-config"].detail)

    def test_root_only_greetd_config_reports_permission_gate_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "etc/greetd/greetd.conf"
            path.parent.mkdir(parents=True)
            path.write_text('command = "private"\n', encoding="utf-8")
            path.chmod(0)

            result = _greetd_check(Path(raw))

            self.assertEqual(result.status, "NOT_RUN")
            self.assertIn("PERMISSION_DENIED", result.detail)

    def test_required_command_missing_fails_but_optional_provider_does_not(self) -> None:
        results = collect_checks(
            root=Path("/nonexistent"),
            which=lambda _name: None,
            runner=lambda argv: subprocess.CompletedProcess(argv, 1, stdout="", stderr=""),
        )
        self.assertEqual(exit_code(results), 1)
        self.assertEqual({x.status for x in results if x.name.startswith("optional-command:")}, {"WARN"})

    def test_safe_runner_rejects_state_changes(self) -> None:
        with self.assertRaises(ValueError):
            safe_run(("/usr/bin/systemctl", "reboot", "--firmware-setup"))
        with self.assertRaises(ValueError):
            safe_run(("/usr/bin/systemctl", "restart", "greetd"))


if __name__ == "__main__":
    unittest.main()
