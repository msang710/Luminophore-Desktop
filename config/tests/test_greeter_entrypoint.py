from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from unittest.mock import patch

from luminophore_shell import __main__ as entrypoint
from luminophore_shell.luminophore_greeter import GreeterLocalConfig, GreeterTheme
from luminophore_shell.greeter_supervisor import SupervisorResult


class GreeterEntrypointTests(unittest.TestCase):
    def test_private_shell_dispatches_both_greeter_components(self):
        with patch('luminophore_shell.greeter_supervisor.main', return_value=7) as supervisor:
            self.assertEqual(entrypoint.main(['greeter-session']), 7)
            supervisor.assert_called_once_with([])
        with patch('luminophore_shell.luminophore_greeter.main', return_value=9) as greeter:
            self.assertEqual(entrypoint.main(['greeter']), 9)
            greeter.assert_called_once_with([])
        with patch('luminophore_shell.luminophore_greeter.main', return_value=9) as greeter:
            self.assertEqual(entrypoint.main(['greeter', '--theme', '/tmp/theme.json']), 9)
            greeter.assert_called_once_with(['--theme', '/tmp/theme.json'])

    def test_real_supervisor_parser_accepts_the_deployed_no_option_command(self):
        with patch.object(sys, 'argv', ['luminophore-shell', 'greeter-session']), \
             patch('luminophore_shell.greeter_supervisor.supervise',
                   return_value=SupervisorResult(0, 0)) as supervise:
            self.assertEqual(entrypoint.main(), 0)
            supervise.assert_called_once()

    def test_real_greeter_parser_accepts_default_command_without_leaking_argv_to_gtk(self):
        from luminophore_shell import luminophore_greeter as ui
        for args in (['greeter'], ['greeter', '--theme', '/tmp/theme.json']):
            with self.subTest(args=args), \
                 patch.object(sys, 'argv', ['luminophore-shell', *args]), \
                 patch.object(ui.Gtk, 'disable_portals'), \
                 patch.object(ui.GreeterTheme, 'read'), \
                 patch.object(ui.GreeterLocalConfig, 'read'), \
                 patch.object(ui, 'LuminophoreGreeterApplication') as app:
                app.return_value.failed = False
                app.return_value.run.return_value = 0
                self.assertEqual(entrypoint.main(), 0)
                app.return_value.run.assert_called_once_with(['luminophore-greeter'])

    def test_bundled_theme_is_valid_without_a_machine_connector(self):
        source = Path(__file__).parents[2] / 'Luminophore-OS/runtime/defaults/greeter-theme.json'
        self.assertEqual(GreeterTheme.read(source).connector, 'auto')

    def test_missing_default_local_config_selects_only_human_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'greeter.json'
            users = [SimpleNamespace(pw_name='greeter', pw_uid=962, pw_shell='/usr/bin/nologin'),
                     SimpleNamespace(pw_name='louise', pw_uid=1000, pw_shell='/usr/bin/fish')]
            with patch('luminophore_shell.luminophore_greeter.DEFAULT_CONFIG_PATH', path), \
                 patch('luminophore_shell.luminophore_greeter.pwd.getpwall', return_value=users):
                self.assertEqual(GreeterLocalConfig.read(path).login_user, 'louise')


if __name__ == '__main__':
    unittest.main()
