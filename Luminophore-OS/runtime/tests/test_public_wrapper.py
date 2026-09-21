from pathlib import Path
import runpy
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luminophore_runtime import cli, installed


class ExecReplacedProcess(Exception):
    pass


class PublicWrapperTests(unittest.TestCase):
    def setUp(self):
        script = Path(__file__).resolve().parents[3] / 'compositor/luminophore/packaging/arch/luminophore-session'
        with patch.object(sys, 'path', list(sys.path)):
            self.module = runpy.run_path(str(script), run_name='wrapper_test')
        self.main = self.module['main']
        self.record = {'generation': 'a' * 64, 'owner': '123:456', 'config_root': '/private/user-config',
                       'settings_environment': {'LUMINOPHORE_SETTINGS_STATE_ROOT': '/private/recovery/state',
                                                'LUMINOPHORE_SETTINGS_RECOVERY': '1'}}

    def test_read_only_control_and_launch_hotkeys_use_admitted_live_generation(self):
        cases = [('control', ['-j', 'monitors']), ('shell_cli', ['ctl', 'status']),
                 ('shell_cli', ['launch', '--', 'ghostty']), ('shell_cli', ['launch-bundle', 'work']),
                 ('shell_cli', ['capture', 'region', '--save']), ('shell_cli', ['restart'])]
        for component, args in cases:
            with self.subTest(component=component, args=args), patch.dict(os.environ), \
                    patch.dict(self.main.__globals__, check=lambda **kwargs: self.record), \
                    patch.object(sys, 'argv', ['luminophore-session', 'component', component, *args]), \
                    patch.object(installed, 'InstalledStore') as store, \
                    patch.object(cli, 'main', side_effect=AssertionError('full admission on hotkey')), \
                    patch('os.set_inheritable') as inheritable, \
                    patch('os.execve', side_effect=ExecReplacedProcess) as execute:
                session = store.return_value.interactive_session.return_value.__enter__.return_value
                session.lease_fd = 123
                session.command.return_value = (['/private/bin/entry', *args], {'PATH':'/usr/bin:/bin'})
                with self.assertRaises(ExecReplacedProcess):
                    self.main()
                store.return_value.interactive_session.assert_called_once_with(self.record)
                session.command.assert_called_once_with(component, args)
                self.assertEqual(os.environ['LUMINOPHORE_SETTINGS_STATE_ROOT'],'/private/recovery/state')
                self.assertEqual(os.environ['LUMINOPHORE_SETTINGS_RECOVERY'],'1')
                inheritable.assert_called_once_with(123, True)
                execute.assert_called_once()

    def test_daemon_and_settings_cannot_bypass_full_admission(self):
        for component, args in [('shell', []), ('shell_cli', ['daemon']), ('shell_cli', ['settings']), ('settings', [])]:
            with self.subTest(component=component, args=args), patch.dict(os.environ), \
                    patch.dict(self.main.__globals__, check=lambda **kwargs: self.record,
                               load=lambda path: {'compatibility':{'config':1}}), \
                    patch.object(sys, 'argv', ['luminophore-session', 'component', component, *args]), \
                    patch.object(installed, 'InstalledStore', side_effect=AssertionError('fast service admission')), \
                    patch.object(cli, 'main', return_value=0) as full:
                self.assertEqual(self.main(), 0)
                passed = full.call_args.args[0]
                self.assertEqual(passed[passed.index('--generation') + 1], self.record['generation'])
                self.assertEqual(passed[passed.index('--component') + 1], component)
