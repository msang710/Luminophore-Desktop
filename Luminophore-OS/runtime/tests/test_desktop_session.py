import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luminophore_runtime.common import ContractError


class DesktopSessionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('luminophore_runtime.desktop_session'),
                             'desktop session lifecycle implementation required')
        from luminophore_runtime import desktop_session
        self.module = desktop_session
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = {'XDG_RUNTIME_DIR': str(self.root), 'HOME': str(self.root / 'home'),
                            'XDG_SESSION_ID': 'current', 'LUMINOPHORE_RELEASE_ID': 'a' * 64}
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[:2] == ['/usr/bin/loginctl', 'show-user']:
            return subprocess.CompletedProcess(argv, 0, 'Sessions=current\n', '')
        if argv[:4] == ['/usr/bin/systemctl', '--user', 'list-units', '--type=service']:
            return subprocess.CompletedProcess(argv, 0, '', '')
        return subprocess.CompletedProcess(argv, 0, '', '')

    def test_cleanup_stops_only_autostart_units_started_by_this_session(self):
        listings = iter([
            'app-existing@autostart.service loaded active running Existing\n',
            'app-existing@autostart.service loaded active running Existing\n'
            'app-steam@autostart.service loaded active running Steam\n',
        ])
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[:2] == ['/usr/bin/loginctl', 'show-user']:
                return subprocess.CompletedProcess(argv, 0, 'Sessions=current\n', '')
            if argv[:4] == ['/usr/bin/systemctl', '--user', 'list-units', '--type=service']:
                return subprocess.CompletedProcess(argv, 0, next(listings), '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        with self.module.Lifecycle(self.environment, runner=runner) as lifecycle:
            lifecycle.environment_path.touch()
        commands = [tuple(row[0]) for row in self.calls]
        self.assertIn(("/usr/bin/systemctl", "--user", "stop",
                       "app-steam@autostart.service", "xdg-desktop-autostart.target"), commands)
        self.assertFalse(any("app-existing@autostart.service" in command for command in commands
                             if "stop" in command))

    def test_lock_rejects_second_session_and_releases_after_failure(self):
        with self.module.Lifecycle(self.environment, runner=self.runner) as first:
            with self.assertRaises(ContractError):
                with self.module.Lifecycle(self.environment, runner=self.runner):
                    self.fail('second session acquired ownership')
            self.assertTrue(first.record_path.exists())
        with self.module.Lifecycle(self.environment, runner=self.runner):
            pass

    def test_other_graphical_login_refuses_before_starting_services(self):
        def runner(argv, **kwargs):
            if argv[1] == 'show-user':
                return subprocess.CompletedProcess(argv, 0, 'Sessions=current other\n', '')
            return subprocess.CompletedProcess(argv, 0,
                f'Type=wayland\nState=active\nUser={os.getuid()}\n', '')
        with self.assertRaisesRegex(ContractError, 'graphical session'):
            with self.module.Lifecycle(self.environment, runner=runner):
                self.fail('overlapping graphical session')

    def test_ready_and_cleanup_touch_only_owned_services(self):
        with self.module.Lifecycle(self.environment, runner=self.runner) as lifecycle:
            env = {**self.environment, **lifecycle.environment,
                   'LUMINOPHORE_INSTANCE_SIGNATURE': 'abc_123_456',
                   'WAYLAND_DISPLAY': 'wayland-2', 'XDG_CURRENT_DESKTOP': 'Luminophore',
                   'LD_LIBRARY_PATH': '/private', 'PYTHONHOME': '/private/python',
                   'LUMINOPHORE_SETTINGS_STATE_ROOT': str(self.root/'recovery/state'),
                   'LUMINOPHORE_SETTINGS_RECOVERY': '1'}
            (self.root / 'luminophore/abc_123_456').mkdir()
            self.module.ready(env, runner=self.runner)
            public = {'XDG_RUNTIME_DIR': str(self.root), 'LUMINOPHORE_INSTANCE_SIGNATURE': 'abc_123_456'}
            self.assertEqual(self.module.check(public, public_control=True)['generation'], 'a' * 64)
            self.assertIn(['/usr/bin/systemctl', '--user', 'restart', 'xdg-desktop-portal-gtk.service',
                           'xdg-desktop-portal.service'], [row[0] for row in self.calls])
            text = lifecycle.environment_path.read_text()
            self.assertIn('LUMINOPHORE_INSTANCE_SIGNATURE="abc_123_456"', text)
            self.assertIn('LUMINOPHORE_SETTINGS_STATE_ROOT=',text)
            self.assertIn('LUMINOPHORE_SETTINGS_RECOVERY="1"',text)
            self.assertNotIn('LD_LIBRARY_PATH', text)
            self.assertNotIn('PYTHONHOME', text)
        commands = [row[0] for row in self.calls]
        self.assertIn(['/usr/bin/systemctl', '--user', 'start', 'luminophore-session.target'], commands)
        self.assertIn(['/usr/bin/systemctl', '--user', 'stop', 'luminophore-session.target'], commands)
        self.assertFalse(lifecycle.environment_path.exists())
        self.assertFalse(any('graphical-session.target' in command for command in commands))

    def test_stale_ready_token_cannot_start_services(self):
        with self.module.Lifecycle(self.environment, runner=self.runner):
            with self.assertRaises(ContractError):
                self.module.ready({**self.environment, 'LUMINOPHORE_SESSION_TOKEN': 'stale'}, runner=self.runner)
        self.assertFalse(any('start' in argv for argv, _ in self.calls))

    def test_watchdog_restart_restarts_services_for_new_instance(self):
        with self.module.Lifecycle(self.environment, runner=self.runner) as lifecycle:
            for signature in ('first_123_456', 'second_123_456'):
                (self.root / 'luminophore' / signature).mkdir()
                self.module.ready({**self.environment, **lifecycle.environment,
                    'LUMINOPHORE_INSTANCE_SIGNATURE': signature, 'WAYLAND_DISPLAY': 'wayland-2'},
                    runner=self.runner)
            commands = [argv for argv, _ in self.calls if argv[0] == '/usr/bin/systemctl'
                        and 'luminophore-session.target' in argv]
            self.assertEqual([argv[2] for argv in commands], ['start', 'stop', 'start'])

    def test_symlink_runtime_directory_is_rejected(self):
        other = self.root / 'other'
        other.mkdir()
        (self.root / 'luminophore').symlink_to(other)
        with self.assertRaises(ContractError):
            with self.module.Lifecycle(self.environment, runner=self.runner):
                self.fail('symlink accepted')

    def test_config_root_resolution_preserves_existing_data(self):
        path=self.module.prepare_config(self.root/'release',self.environment)
        self.assertEqual(path,self.root/'home/.config/luminophore')
        self.assertFalse(path.exists())
        path.mkdir(parents=True)
        source=path/'settings.toml';source.write_text('schema_version=1\n')
        self.module.prepare_config(self.root/'release',self.environment)
        self.assertEqual(source.read_text(),'schema_version=1\n')
        with self.assertRaisesRegex(ContractError,'schema'):
            self.module.prepare_config(self.root/'release',self.environment,config_version=2)
