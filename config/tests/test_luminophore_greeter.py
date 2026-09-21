from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.luminophore_greeter import (
    GreeterConfigurationError,
    GreeterLocalConfig,
    GreeterTheme,
    VISIBLE_CONTROLS,
    main,
    positioned_row_bounds,
)


def theme_payload() -> dict[str, object]:
    return {
        "version": 1,
        "primary_connector": "DP-2",
        "colors": {
            "primary": "#78DCE8",
            "secondary": "#AB9DF2",
            "surface": "#0A0D12",
            "on_surface": "#F3F7FA",
        },
        "placement": {
            "center_from_right": [2, 3],
            "center_from_top": [2, 3],
        },
        "layout": {
            "entry_width": 360,
            "control_size": 48,
            "spacing": 10,
            "radius": 12,
            "glow_intensity": 2.3,
            "glow_radius": 64,
            "outline_width": 5,
            "palette_transition_ms": 594,
            "expansion_ms": 200,
            "backdrop_opacity": 0.4,
        },
    }


class LuminophoreGreeterContractTests(unittest.TestCase):
    def test_theme_and_hidden_account_are_separate_validated_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            theme_path = root / "theme.json"
            local_path = root / "local.json"
            theme_path.write_text(json.dumps(theme_payload()), encoding="utf-8")
            local_path.write_text(json.dumps({"version": 1, "login_user": "testuser"}), encoding="utf-8")

            theme = GreeterTheme.read(theme_path)
            local = GreeterLocalConfig.read(local_path)

            self.assertEqual(theme.connector, "DP-2")
            self.assertEqual(theme.expansion_ms, 200)
            self.assertEqual(local.login_user, "testuser")
            self.assertNotIn("login_user", theme_path.read_text(encoding="utf-8"))

    def test_wrong_placement_and_invalid_account_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = theme_payload()
            payload["placement"] = {"center_from_right": [1, 2]}
            theme_path = root / "theme.json"
            theme_path.write_text(json.dumps(payload), encoding="utf-8")
            local_path = root / "local.json"
            local_path.write_text(json.dumps({"version": 1, "login_user": "Bad User"}), encoding="utf-8")

            with self.assertRaisesRegex(GreeterConfigurationError, "invalid_placement"):
                GreeterTheme.read(theme_path)
            with self.assertRaisesRegex(GreeterConfigurationError, "invalid_local_config"):
                GreeterLocalConfig.read(local_path)

    def test_unknown_theme_or_local_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = theme_payload()
            payload["login_user"] = "must-not-be-here"
            theme_path = root / "theme.json"
            theme_path.write_text(json.dumps(payload), encoding="utf-8")
            local_path = root / "local.json"
            local_path.write_text(
                json.dumps({"version": 1, "login_user": "testuser", "display": True}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(GreeterConfigurationError, "invalid_theme"):
                GreeterTheme.read(theme_path)
            with self.assertRaisesRegex(GreeterConfigurationError, "invalid_local_config"):
                GreeterLocalConfig.read(local_path)

    def test_dp2_row_center_is_two_thirds_from_right_and_top(self) -> None:
        x, y, width, height = positioned_row_bounds(1920, 1080, 540, 48)

        self.assertEqual((x, y, width, height), (370, 696, 540, 48))
        self.assertEqual(x + width // 2, 640)
        self.assertEqual(y + height // 2, 720)

    def test_visible_contract_has_only_password_and_three_power_actions(self) -> None:
        source = (Path(__file__).parents[1] / "luminophore_shell/luminophore_greeter.py").read_text(encoding="utf-8")

        self.assertEqual(VISIBLE_CONTROLS, ("password", "reboot", "poweroff", "firmware"))
        self.assertIn("entry.set_visibility(False)", source)
        self.assertIn('Gtk4LayerShell.set_namespace(window, "luminophore-shell-login")', source)
        self.assertIn("Gtk4LayerShell.set_monitor(window, monitor)", source)
        self.assertNotIn("Gtk.Label(", source)
        self.assertNotIn("combo", source.casefold())

    def test_main_disables_portals_before_constructing_application(self) -> None:
        events: list[str] = []

        class FakeApplication:
            failed = False

            def __init__(self, *_args, **_kwargs) -> None:
                events.append("application")

            def run(self, _argv) -> int:
                return 0

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("luminophore_shell.luminophore_greeter.Gtk.disable_portals", side_effect=lambda: events.append("portals")),
            patch("luminophore_shell.luminophore_greeter.GreeterTheme.read", return_value=GreeterTheme("DP-2", "#111111", "#222222", "#333333", "#EEEEEE")),
            patch("luminophore_shell.luminophore_greeter.LuminophoreGreeterApplication", FakeApplication),
        ):
            self.assertEqual(main(["--theme", str(Path(directory) / "theme.json"), "--test-mode"]), 0)

        self.assertEqual(events, ["portals", "application"])


if __name__ == "__main__":
    unittest.main()
