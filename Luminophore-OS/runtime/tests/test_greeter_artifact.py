from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luminophore_runtime import desktop
from luminophore_runtime.common import ContractError
from luminophore_runtime.session import Session


class GreeterArtifactTests(unittest.TestCase):
    def setUp(self):
        self.entries = desktop.desktop_entries()
        self.files = {
            "bin/start-hyprland", "bin/Hyprland", "bin/hyprctl", "python/bin/python3",
            "shell/luminophore-shell", "config/greeter/settings.toml", "config/greeter-theme.json",
            "share/fonts/luminophore/fallback.ttf",
        }

        self.files.update(desktop.GREETER_ASSETS)

    def test_private_greeter_components_are_same_release_paths(self):
        result = desktop.validate_greeter_contract(self.entries, self.files)
        self.assertEqual(set(result), set(desktop.GREETER_ENTRIES))
        text = repr(result)
        self.assertNotIn("/usr/bin/start-hyprland", text)
        self.assertNotIn("/usr/bin/hyprctl", text)
        self.assertNotIn("hyprland.desktop", text)
        self.assertEqual(
            result["greeter_compositor"]["args"][-1],
            "{release}/config/greeter/settings.toml",
        )

    def test_missing_component_and_system_hyprland_path_fail_closed(self):
        entries = dict(self.entries); entries.pop("greeter_control")
        with self.assertRaisesRegex(ContractError, "greeter"):
            desktop.validate_greeter_contract(entries, self.files)
        entries = desktop.desktop_entries()
        entries["greeter_compositor"] = {"path":"/usr/bin/start-hyprland","args":[]}
        with self.assertRaises(ContractError):
            desktop.validate_greeter_contract(entries, self.files)

    def test_missing_release_asset_fails_closed(self):
        self.files.remove("share/fonts/luminophore/fallback.ttf")
        with self.assertRaisesRegex(ContractError, "asset"):
            desktop.validate_greeter_contract(self.entries, self.files)

    def test_greeter_config_does_not_start_an_unobserved_child(self):
        source = Path(__file__).resolve().parents[1] / "defaults/greeter.lua"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("hl.exec_cmd", text)
        self.assertNotIn('hl.on("hyprland.start"', text)
        runner = (source.parents[1] / "files/luminophore-greeter-session").read_text()
        self.assertIn("--component greeter_session", runner)
        for forbidden in (
            "autostart.lua", "hyprland.lua", "luminophore_owned",
            "luminophore_user", "luminophore-session.target", "hyprpaper",
        ):
            self.assertNotIn(forbidden, text)

    def test_greeter_python_entries_receive_the_private_runtime(self):
        release = Path("/release")
        manifest = {
            "generation": "a" * 64,
            "entries": self.entries,
            "runtime_env": {
                "PYTHONHOME": "python",
                "GI_TYPELIB_PATH": "lib/girepository-1.0",
            },
            "compatibility": {"config": 1},
        }
        session = Session(release, manifest, 9, "/store", "/")
        with patch("luminophore_runtime.artifact.verify"), patch(
            "luminophore_runtime.artifact.preflight"
        ):
            for component in ("greeter_shell", "greeter_session"):
                _, environment = session.command(component, environment=os.environ)
                self.assertEqual(environment["PYTHONHOME"], "/release/python")
                self.assertEqual(
                    environment["GI_TYPELIB_PATH"],
                    "/release/lib/girepository-1.0",
                )


if __name__ == "__main__": unittest.main()
