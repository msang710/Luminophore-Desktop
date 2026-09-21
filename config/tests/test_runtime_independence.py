from __future__ import annotations

from pathlib import Path
import re
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_SHELL_TOKEN = "noc" + "talia"


def runtime_sources() -> list[Path]:
    paths: list[Path] = []
    for relative in ("luminophore_shell", "scripts", "systemd", "tests"):
        for path in (PROJECT_ROOT / relative).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                paths.append(path)
    paths.extend(PROJECT_ROOT.glob("*.lua"))
    paths.extend(path for path in PROJECT_ROOT.glob("luminophore-*") if path.is_file())
    paths.append(PROJECT_ROOT.parent / "hyprland.lua")
    return sorted(set(paths))


class RuntimeIndependenceTests(unittest.TestCase):
    def test_runtime_config_and_tests_have_no_legacy_shell_reference(self) -> None:
        offenders = []
        for path in runtime_sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            if LEGACY_SHELL_TOKEN in text.casefold():
                offenders.append(str(path.relative_to(PROJECT_ROOT.parent)))
        self.assertEqual(offenders, [])

    def test_command_spy_finds_only_independent_session_commands(self) -> None:
        command_pattern = re.compile(r"(?:exec_cmd|ExecStart|subprocess\.(?:run|Popen))")
        commands = []
        for path in runtime_sources():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if command_pattern.search(line):
                    commands.append((path, line))
        self.assertTrue(commands, "command spy did not inspect any launch path")
        self.assertEqual(
            [(str(path), line) for path, line in commands if LEGACY_SHELL_TOKEN in line.casefold()],
            [],
        )

    def test_runtime_binds_use_luminophore_shell_hardware_ipc(self) -> None:
        text = (PROJECT_ROOT / "binds.lua").read_text(encoding="utf-8")
        for action in (
            "volume-up", "volume-down", "volume-mute", "mic-mute",
            "media-toggle", "media-next", "media-previous",
            "brightness-preview-up", "brightness-preview-down", "brightness-commit",
        ):
            self.assertIn(f"hardware {action}", text)
        self.assertGreaterEqual(text.count("release = true"), 3)
        self.assertIn("toggle overview", text)
        self.assertIn("open launcher --provider emoji", text)
        self.assertIn("wallpaper open", text)

    def test_hyprland_theme_is_owned_by_luminophore_module(self) -> None:
        root = PROJECT_ROOT.parent
        entrypoint = (root / "hyprland.lua").read_text(encoding="utf-8")
        decorations = (PROJECT_ROOT / "decorations.lua").read_text(encoding="utf-8")
        theme = (PROJECT_ROOT / "luminophore_theme.lua").read_text(encoding="utf-8")
        self.assertNotIn(LEGACY_SHELL_TOKEN, entrypoint.casefold())
        self.assertIn('require("config.luminophore_theme")', decorations)
        for semantic in ("primary", "surface_container", "secondary", "error"):
            self.assertIn(f"{semantic} =", theme)


if __name__ == "__main__":
    unittest.main()
