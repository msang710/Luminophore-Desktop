from __future__ import annotations

from pathlib import Path
import unittest


class SessionUnitTests(unittest.TestCase):
    def test_session_owns_input_method_and_wallpaper_and_enables_native_bloom(self):
        target = Path('systemd/luminophore-session.target').read_text()
        for name in ('luminophore-input-method.service', 'luminophore-wallpaper.service',
                     'luminophore-idle-lock.service', 'luminophore-secret-service.service'):
            self.assertIn(name, target)
            unit = Path('systemd', name).read_text()
            self.assertIn('PartOf=luminophore-session.target', unit)
            self.assertIn('EnvironmentFile=%t/luminophore/session.env', unit)
        self.assertIn('LUMINOPHORE_SHELL_BLOOM=1', Path('systemd/luminophore-shell.service').read_text())

    def test_lock_uses_ext_session_lock_pam_provider_and_idle_before_sleep(self) -> None:
        lock = Path("systemd/luminophore-session-lock.service").read_text()
        idle = Path("systemd/luminophore-idle-lock.service").read_text()
        self.assertIn("ExecStart=/usr/bin/gtklock -c /usr/share/luminophore/gtklock.ini", lock)
        self.assertIn("PartOf=luminophore-session.target", lock)
        self.assertIn("ExecStart=/usr/bin/swayidle -w", idle)
        self.assertIn("timeout 300", idle)
        self.assertIn("before-sleep", idle)
        self.assertGreaterEqual(idle.count("luminophore-session-lock.service"), 2)

    def test_power_surface_exposes_session_and_machine_actions(self) -> None:
        text = Path("luminophore_shell/ui/power.py").read_text()
        for action in ("lock", "suspend", "logout", "reboot", "poweroff", "firmware-setup"):
            self.assertIn(f'"{action}"', text)

    def test_compositor_start_activates_luminophore_session_after_dbus_import(self) -> None:
        text = Path("autostart.lua").read_text(encoding="utf-8")
        self.assertIn('LUMINOPHORE_SESSION_READY_COMMAND', text)
        self.assertNotIn('--systemd --all', text)
        self.assertNotIn('start luminophore-session.target', text)

    def test_luminophore_shell_uses_verified_vulkan_pacing_without_changing_greeter(self) -> None:
        shell = Path("systemd/luminophore-shell.service").read_text(encoding="utf-8")
        self.assertIn("Environment=GSK_RENDERER=vulkan", shell)
        self.assertIn("KillMode=control-group", shell)
        self.assertIn("EnvironmentFile=%t/luminophore/session.env", shell)
        self.assertIn("PartOf=luminophore-session.target", shell)
        self.assertNotIn("WantedBy=graphical-session.target", shell)
        other_units = [
            path for path in Path("systemd").glob("*.service")
            if path.name != "luminophore-shell.service"
        ]
        self.assertTrue(other_units)
        for path in other_units:
            self.assertNotIn("GSK_RENDERER=vulkan", path.read_text(encoding="utf-8"))

    def test_polkit_agent_uses_exact_package_path_and_session_lifecycle(self) -> None:
        text = Path("systemd/luminophore-polkit-agent.service").read_text(encoding="utf-8")
        self.assertIn("ExecCondition=/usr/bin/test -x /usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1", text)
        self.assertIn("ExecStart=/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1", text)
        self.assertIn("PartOf=luminophore-session.target", text)
        self.assertIn("PartOf=luminophore-session.target", text)

    def test_session_target_does_not_apply_unapproved_color_temperature(self) -> None:
        text = Path("systemd/luminophore-session.target").read_text(encoding="utf-8")
        self.assertIn(
            "Wants=luminophore-shell.service luminophore-spotify.service luminophore-polkit-agent.service",
            text,
        )
        self.assertNotIn("hyprsunset.service", text)
        self.assertNotIn("hypridle", text)
        self.assertNotIn("hyprlock", text)

    def test_spotify_is_an_independent_graphical_session_service(self) -> None:
        text = Path("systemd/luminophore-spotify.service").read_text(encoding="utf-8")
        self.assertIn("PartOf=luminophore-session.target", text)
        self.assertIn("WantedBy=luminophore-session.target", text)
        self.assertIn("ExecCondition=/usr/bin/flatpak info com.spotify.Client", text)
        self.assertIn("Type=oneshot", text)
        self.assertIn("ExecStart=/usr/bin/flatpak run", text)
        self.assertIn("com.spotify.Client --minimized", text)
        self.assertIn("RemainAfterExit=yes", text)
        self.assertNotIn("ExecStop=/usr/bin/flatpak kill", text)
        self.assertNotIn("luminophore-shell.service", text)

    def test_spotify_window_rule_never_activates_background_workspace(self) -> None:
        text = Path("windowrules.lua").read_text(encoding="utf-8")
        spotify_rule = text.split('name = "luminophore-spotify-background"', 1)[1].split("})", 1)[0]
        self.assertIn('workspace = "special:luminophore-spotify silent"', spotify_rule)
        self.assertIn("no_initial_focus = true", spotify_rule)
        self.assertNotIn("no_focus = true", spotify_rule)

    def test_luminophore_shell_deploy_and_rollback_include_spotify_unit(self) -> None:
        deploy = Path("scripts/deploy-luminophore-shell").read_text(encoding="utf-8")
        rollback = Path("scripts/rollback-luminophore-shell").read_text(encoding="utf-8")
        self.assertIn('systemd/luminophore-spotify.service', deploy)
        self.assertIn('enable --now luminophore-shell.service luminophore-spotify.service', deploy)
        self.assertIn('disable --now luminophore-shell.service luminophore-spotify.service', rollback)
        self.assertIn('"$user_unit_dir/luminophore-spotify.service"', rollback)


if __name__ == "__main__":
    unittest.main()
