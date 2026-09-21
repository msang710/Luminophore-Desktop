import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock, patch

from luminophore_shell.app_bundles import BundleStore, encode, execute, running, validate


def recipe(*items):
    return dict(id='development', name='작업', chord='', items=[dict(desktop_id=i, new_instance=False) for i in items])


def app(key, wm=''):
    return NS(desktop_id=key, window_class=wm)


class BundleTests(TestCase):
    def test_arbitrary_apps_partial_failure_and_exact_skip(self):
        apps = [app('org.other.App.desktop'), app('running.desktop', 'Exact'), app('fail.desktop')]
        launcher = Mock()
        launcher.launch_desktop.side_effect = [True, False]
        windows = Mock(return_value=[NS(app_class='Exact', initial_class='Exact')])
        result = execute(recipe(*(a.desktop_id for a in apps)), apps, windows, launcher)
        self.assertEqual(result, dict(launched=['org.other.App.desktop'], skipped=['running.desktop'], failed=['fail.desktop']))
        self.assertEqual(launcher.launch_desktop.call_count, 2)

    def test_new_instance_requests_even_when_running(self):
        b = recipe('app.desktop'); b['items'][0]['new_instance'] = True
        launcher = Mock(); launcher.launch_desktop.return_value = True
        result = execute(b, [app('app.desktop')], Mock(side_effect=AssertionError('not needed')), launcher)
        self.assertEqual(result['launched'], ['app.desktop'])

    def test_no_substring_matching(self):
        self.assertFalse(running(app('terminal.desktop'), [NS(app_class='terminal-helper', initial_class='other')]))

    def test_query_unknown_never_launches_or_retries(self):
        launcher = Mock()
        result = execute(recipe('app.desktop'), [app('app.desktop')], Mock(side_effect=RuntimeError('unknown')), launcher)
        self.assertEqual(result['failed'], ['app.desktop'])
        launcher.launch_desktop.assert_not_called()

    def test_missing_app_does_not_launch(self):
        launcher = Mock()
        self.assertEqual(execute(recipe('gone.desktop'), [], Mock(), launcher)['failed'], ['gone.desktop'])
        launcher.launch_desktop.assert_not_called()

    def test_atomic_store_cas_and_corruption(self):
        from tests.domain_fixture import Domains
        domains = Domains(self)
        store = BundleStore(service=domains)
        _, old = store.snapshot()
        values, current = store.save([recipe('x.desktop')], old)
        self.assertEqual(values[0]['items'][0]['desktop_id'], 'x.desktop')
        with self.assertRaises(ValueError): store.save([], old)
        domains.fail = OSError('disk')
        with self.assertRaises(OSError): store.save([], current)
        self.assertEqual(store.snapshot()[1], current)
        path = domains.store.generations/current/'bundles.toml'
        path.write_text('schema_version=1\nbundles=[]')
        with self.assertRaises(ValueError): store.snapshot()

    def test_invalid_duplicate_and_lua_injection(self):
        for key in ['../../bad.desktop', 'x\n.desktop', '-bad.desktop']:
            with self.assertRaises(ValueError): validate([recipe(key)])
        with self.assertRaises(ValueError): validate([recipe('x.desktop', 'x.desktop')])
        b = recipe('x.desktop'); b['chord'] = 'SUPER + "); os.exit()'
        with self.assertRaises(ValueError): encode([b])

    def test_conflicting_shortcut_rejected_before_write(self):
        from tests.domain_fixture import Domains
        domains = Domains(self); store = BundleStore(service=domains)
        before = store.snapshot()
        b = recipe('x.desktop'); b['chord'] = 'SUPER + Q'
        with self.assertRaises(ValueError): store.save([b], before[1])
        self.assertEqual(store.snapshot(), before)

    def test_group_forwarded_with_token_request(self):
        from luminophore_shell.external_launch import UwsmApplicationLauncher
        with patch('luminophore_shell.hyprland.HyprlandClient') as client:
            client.return_value._run.return_value = 'token'
            self.assertEqual(UwsmApplicationLauncher._direct_launch_token('abc-123'), 'token')
            client.return_value._run.assert_called_once_with('luminophorelaunchtoken', 'abc-123')

    def test_lua_bundle_shortcuts_preserve_existing_actions(self):
        import os
        import shutil
        import subprocess
        if not shutil.which('lua'):
            self.skipTest('lua unavailable')
        script = '''
TERMINAL, FILE_MANAGER, EDITOR, CALCULATOR, BROWSER = "term", "files", "edit", "calc", "browser"
local proxy = {}
setmetatable(proxy, { __index = function() return proxy end, __call = function() return proxy end })
hl = proxy
hl.dsp = proxy
local counts = {}
hl.bind = function(chord) counts[chord] = (counts[chord] or 0) + 1 end
package.preload["config.luminophore_bundles"] = function() return {
 { id="blocked", chord="SUPER + Q" },
 { id="work", chord="SUPER + CTRL + 1" }
} end
dofile("binds.lua")
assert(counts["SUPER + Q"] == 1)
assert(counts["SUPER + CTRL + 1"] == 1)
'''
        subprocess.run(['lua', '-e', script], check=True, capture_output=True, env={**os.environ, 'LUMINOPHORE_COMPOSITOR':'1'})

    def test_cli_has_bundle_command(self):
        from luminophore_shell.__main__ import _parser, main
        self.assertEqual(_parser().parse_args(['launch-bundle', 'work']).identifier, 'work')
        with patch('luminophore_shell.app_bundles.launch_bundle', return_value=dict(launched=['other.desktop'], skipped=[], failed=[])) as launch:
            self.assertEqual(main(['launch-bundle', 'work']), 0)
            launch.assert_called_once_with('work')

    def test_force_close_binding_uses_its_own_compositor_control(self):
        import os
        import subprocess
        script = '''
TERMINAL, FILE_MANAGER, EDITOR, CALCULATOR, BROWSER = "term", "files", "edit", "calc", "browser"
local proxy = {}
setmetatable(proxy, { __index = function() return proxy end, __call = function() return proxy end })
hl = { dsp = proxy, bind = function() end }
local commands = {}
hl.dsp.exec_cmd = function(command) commands[#commands + 1] = command; return proxy end
dofile("binds.lua")
local expected = os.getenv("EXPECTED_KILL")
local found = false
for _, command in ipairs(commands) do
    if command == expected then found = true end
    if command:match(" kill$") then assert(command == expected, command) end
end
assert(found, "missing compositor-specific kill command")
'''
        for mode, control, expected in [('1', None, '/usr/bin/luminophorectl kill'),
                                        ('1', '/release/bin/hyprctl', '/release/bin/hyprctl kill'),
                                        ('0', None, 'hyprctl kill')]:
            with self.subTest(mode=mode, control=control):
                env = {**os.environ, 'LUMINOPHORE_COMPOSITOR': mode, 'EXPECTED_KILL': expected}
                env.pop('LUMINOPHORE_CONTROL_BIN', None)
                if control:
                    env['LUMINOPHORE_CONTROL_BIN'] = control
                subprocess.run(['lua', '-e', script], check=True, capture_output=True, env=env)
