from pathlib import Path
import unittest

from luminophore_runtime import packaging


class PackageLayoutTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Path(__file__).resolve().parents[1]
        self.migration = self.runtime.parents[1]
        self.assets = self.migration / "compositor/luminophore/packaging/arch"
        self.units = self.migration / "config/systemd"
        self.files = packaging.package_files(self.migration, self.assets, self.units)
        self.destinations = {destination for _, destination, _ in self.files}

    def test_declared_sources_exist_and_destinations_are_unique(self):
        self.assertEqual(len(self.destinations), len(self.files))
        for source, destination, mode in self.files:
            self.assertTrue(source.is_file(), source)
            self.assertFalse(source.is_symlink(), source)
            self.assertFalse(destination.startswith("/"), destination)
            self.assertIn(mode, {0o644, 0o755})

    def test_package_owns_greeter_and_nonblocking_update_observer(self):
        required = {
            "usr/lib/luminophore/runtime/luminophore-runtime",
            "usr/lib/luminophore/luminophore-greeter-session",
            "usr/lib/luminophore/greetd/luminophore.toml",
            "usr/share/luminophore/desktop-capabilities.json",
            "usr/share/libalpm/hooks/95-luminophore-update-observer.hook",
            "usr/lib/luminophore-update-guardian/luminophore-update-observe",
            "usr/lib/luminophore-update-guardian/desktop-follow",
            "usr/lib/luminophore-update-guardian/desktop-follow.py",
            "usr/lib/luminophore-update-guardian/build-next",
            "usr/lib/luminophore-update-guardian/install-release",
            "usr/lib/systemd/system/luminophore-update-guardian.path",
            "usr/lib/systemd/system/luminophore-update-guardian.service",
            "usr/lib/systemd/system/luminophore-release-install@.service",
            "usr/lib/sysusers.d/luminophore-update-guardian.conf",
            "usr/lib/tmpfiles.d/luminophore-update-guardian.conf",
        }
        self.assertLessEqual(required, self.destinations)
        self.assertNotIn("etc/greetd/config.toml", self.destinations)
        self.assertFalse(any("protect" in value.casefold() for value in self.destinations))

    def test_layout_never_claims_hyprland_or_activates_services(self):
        forbidden = {
            "usr/bin/Hyprland", "usr/bin/hyprctl",
            "usr/share/wayland-sessions/hyprland.desktop",
            "etc/greetd/config.toml",
        }
        self.assertTrue(self.destinations.isdisjoint(forbidden))
        source = (self.runtime / "luminophore_runtime/packaging.py").read_text(encoding="utf-8")
        for command in ("systemctl enable", "systemctl restart", "pacman -S"):
            self.assertNotIn(command, source)

    def test_public_luminophore_command_is_generated(self):
        source = (self.runtime / "luminophore_runtime/packaging.py").read_text(encoding="utf-8")
        self.assertIn("('luminophore', '')", source)

    def test_desktop_session_uses_the_release_entry_config(self):
        source = (self.runtime / "luminophore_runtime/cli.py").read_text(encoding="utf-8")
        self.assertNotIn("LUMINOPHORE_CONFIG_ROOT']) / 'hyprland.lua", source)


if __name__ == "__main__":
    unittest.main()
