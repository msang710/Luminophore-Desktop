from pathlib import Path
import os
import subprocess
import tempfile
import unittest


GENERATOR = Path('/usr/lib/systemd/user-generators/systemd-xdg-autostart-generator')
CONDITION = Path('/usr/lib/systemd/systemd-xdg-autostart-condition')
UNITS = Path(__file__).resolve().parents[3] / 'config/systemd'


class XdgAutostartTests(unittest.TestCase):
    def test_session_opts_into_standard_autostart_without_legacy_graphical_target(self):
        text = (UNITS / 'luminophore-session.target').read_text()
        wants = [value for row in text.splitlines() if row.startswith('Wants=')
                 for value in row.split('=', 1)[1].split()]
        self.assertIn('xdg-desktop-autostart.target', wants)
        self.assertIn('Before=xdg-desktop-autostart.target', text)
        self.assertNotIn('graphical-session.target', text)
        self.assertIn('luminophore-input-method.service', wants)

    def test_static_fcitx_condition_blocks_only_luminophore_and_allows_other_desktops(self):
        dropin = UNITS / 'app-org.fcitx.Fcitx5@autostart.service.d/luminophore.conf'
        text = dropin.read_text()
        expected = f'ExecCondition={CONDITION} "" "Luminophore"'
        self.assertIn(expected, text)
        self.assertNotIn('ExecStart=', text)
        if not CONDITION.is_file(): self.skipTest('systemd XDG condition helper unavailable')
        for desktop, code in [('Luminophore', 1), ('Hyprland', 0), ('GNOME', 0),
                              ('Hyprland:Luminophore', 1), ('', 0)]:
            with self.subTest(desktop=desktop):
                result = subprocess.run([str(CONDITION), '', 'Luminophore'],
                    env={**os.environ, 'XDG_CURRENT_DESKTOP':desktop}, capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, code, result.stderr)

    def test_actual_generator_reuses_user_steam_and_honors_hidden_and_desktop_rules(self):
        if not GENERATOR.is_file(): self.skipTest('systemd XDG generator unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user = root / 'user/autostart'
            system = root / 'system/autostart'
            user.mkdir(parents=True)
            system.mkdir(parents=True)
            def entry(path, name, extra=''):
                path.write_text(f'[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/true\n{extra}')
            entry(system / 'steam.desktop', 'System Steam')
            entry(user / 'steam.desktop', 'User Steam')
            entry(system / 'hidden.desktop', 'System hidden')
            entry(user / 'hidden.desktop', 'User disabled', 'Hidden=true\n')
            entry(user / 'only.desktop', 'GNOME only', 'OnlyShowIn=GNOME;\n')
            entry(user / 'not.desktop', 'Not Luminophore', 'NotShowIn=Luminophore;\n')
            outputs = [root / name for name in ('normal','early','late')]
            for path in outputs: path.mkdir()
            environment = {**os.environ, 'HOME':str(root), 'XDG_CONFIG_HOME':str(user.parent),
                'XDG_CONFIG_DIRS':str(system.parent), 'XDG_CURRENT_DESKTOP':'Luminophore',
                'SYSTEMD_SCOPE':'user'}
            result = subprocess.run([str(GENERATOR), *map(str, outputs)], env=environment,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            services = {p.name:p.read_text() for directory in outputs for p in directory.glob('*.service')}
            self.assertIn('app-steam@autostart.service', services)
            steam = services['app-steam@autostart.service']
            self.assertIn(f'SourcePath={user / "steam.desktop"}', steam)
            self.assertIn('Description=User Steam', steam)
            self.assertNotIn('app-hidden@autostart.service', services)
            self.assertIn(f'ExecCondition={CONDITION} "GNOME" ""', services['app-only@autostart.service'])
            self.assertIn(f'ExecCondition={CONDITION} "" "Luminophore"', services['app-not@autostart.service'])
            self.assertNotIn('Requires=graphical-session.target', steam)
            self.assertNotIn('Wants=graphical-session.target', steam)
            self.assertTrue(any((directory / 'xdg-desktop-autostart.target.wants/app-steam@autostart.service').is_symlink()
                                for directory in outputs))
