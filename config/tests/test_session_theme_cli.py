from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from luminophore_shell.session_theme import LuminophoreGreeterRenderer, RetainSplashInstaller, SessionThemeGeneration
from luminophore_shell.session_theme_cli import main


class SessionThemeCliTests(unittest.TestCase):
    @staticmethod
    def _fake_dependencies(root: Path) -> None:
        for relative in (
            "usr/bin/greetd", "usr/bin/uwsm", "usr/bin/systemctl",
            "usr/lib/luminophore/luminophore-greeter-session",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
        desktop = root / "usr/share/wayland-sessions/luminophore.desktop"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text("[Desktop Entry]\nExec=/usr/bin/luminophore\n", encoding="utf-8")
        passwd = root / "etc/passwd"
        passwd.parent.mkdir(parents=True, exist_ok=True)
        passwd.write_text("testuser:x:1000:1000::/home/testuser:/bin/sh\n", encoding="utf-8")
        typelibs = root / "usr/lib/girepository-1.0"
        typelibs.mkdir(parents=True, exist_ok=True)
        for name in ("Gtk-4.0.typelib", "Gtk4LayerShell-1.0.typelib"):
            (typelibs / name).write_bytes(b"fixture")

    def test_stage_session_routes_to_requested_user_output(self) -> None:
        generation = SessionThemeGeneration(
            "1234567890abcdef1234567890abcdef", "dark", "#78DCE8", "#AB9DF2",
            "#0A0D12", "#F3F7FA", "#FFB4AB",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "wall.png"
            Image.new("RGB", (8, 8), "#123456").save(image)
            rendered = LuminophoreGreeterRenderer().render(root / "stage", generation, ("DP-2",), "DP-2", {"DP-2": image})
            output = root / "output"
            config = root / "config.toml"
            with patch("luminophore_shell.session_theme_cli._stage_current", return_value=rendered) as stage:
                self.assertEqual(main(["stage-session", "--output-root", str(output), "--config", str(config)]), 0)
            stage.assert_called_once_with(output, config)

    def test_check_validates_without_writing_fake_root(self) -> None:
        generation = SessionThemeGeneration(
            "1234567890abcdef1234567890abcdef", "dark", "#78DCE8", "#AB9DF2",
            "#0A0D12", "#F3F7FA", "#FFB4AB",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "wall.png"
            Image.new("RGB", (8, 8), "#123456").save(image)
            rendered = LuminophoreGreeterRenderer().render(root / "stage", generation, ("DP-2",), "DP-2", {"DP-2": image})
            fake_root = root / "fake-root"
            self._fake_dependencies(fake_root)
            self.assertEqual(main([
                "install-session", "--staged", str(rendered.root), "--root", str(fake_root),
                "--login-user", "testuser", "--check",
            ]), 0)
            self.assertFalse((fake_root / "etc/luminophore-shell").exists())
            self.assertFalse((fake_root / "etc/greetd/greetd.conf").exists())
            self.assertFalse((fake_root / "var/lib/luminophore-shell").exists())

    def test_handoff_check_validates_vendor_unit_without_writing_dropin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = root / RetainSplashInstaller.UNIT_PATH.removeprefix("/")
            unit.parent.mkdir(parents=True)
            unit.write_text("[Service]\nExecStart=-/usr/bin/plymouth quit\n", encoding="utf-8")

            self.assertEqual(main(["install-handoff", "--root", str(root), "--check"]), 0)

            dropin = root / RetainSplashInstaller.DROPIN_PATH.removeprefix("/")
            self.assertFalse(dropin.exists())

    def test_preview_uses_only_generated_fake_auth_launcher(self) -> None:
        generation = SessionThemeGeneration(
            "1234567890abcdef1234567890abcdef", "dark", "#78DCE8", "#AB9DF2",
            "#0A0D12", "#F3F7FA", "#FFB4AB",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "wall.png"
            Image.new("RGB", (8, 8), "#123456").save(image)
            rendered = LuminophoreGreeterRenderer().render(
                root / "stage", generation, ("DP-2",), "DP-2", {"DP-2": image},
            )
            with patch("luminophore_shell.session_theme_cli._preview_runner") as runner:
                runner.return_value.returncode = 0

                self.assertEqual(main(["preview-session", "--staged", str(rendered.root)]), 0)

            argv = tuple(runner.call_args.args[0])
            self.assertEqual(argv[0], "/usr/bin/python3")
            self.assertIn("--test-mode", argv)
            self.assertEqual(argv[-1], "failure,success")
            self.assertNotIn("--config", argv)

    def test_live_apply_requires_root(self) -> None:
        with patch("luminophore_shell.session_theme_cli.os.geteuid", return_value=1000):
            self.assertEqual(main(["rollback-session", "--backup", "/missing", "--root", "/", "--apply"]), 1)


if __name__ == "__main__":
    unittest.main()
