import json
import os
from pathlib import Path
import subprocess
import sys
import time

from test_artifact import ArtifactFixture


CLI = Path(__file__).resolve().parents[1] / 'luminophore-runtime'


class CLITests(ArtifactFixture):
    def call(self, *args, **kwargs):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              capture_output=True, text=True, **kwargs)

    def test_pack_stage_select_run_is_one_usable_path(self):
        recipe = self.root / 'recipe.json'
        recipe.write_text(json.dumps(self.recipe))
        packed = self.call('pack', '--sysroot', self.sysroot, '--recipe', recipe,
                           '--output', self.root / 'artifacts')
        self.assertEqual(packed.returncode, 0, packed.stderr)
        release = Path(json.loads(packed.stdout)['release'])
        store = self.root / 'store'
        staged = self.call('stage', '--store', store, '--release', release,
                           '--trusted-generation', release.name)
        self.assertEqual(staged.returncode, 0, staged.stderr)
        selected = self.call('select', '--store', store, '--generation', release.name,
                            '--revision', '1', '--config-version', '1', '--host-root', self.sysroot)
        self.assertEqual(selected.returncode, 0, selected.stderr)
        result = self.call('run', '--store', store, '--component', 'control',
                          '--config-version', '1', '--host-root', self.sysroot)
        self.assertEqual((result.returncode, result.stdout), (0, '42\n'), result.stderr)

    def test_existing_build_entry_can_pack_a_whole_de_without_host_rebuild(self):
        recipe = self.root / 'recipe.json'
        recipe.write_text(json.dumps(self.recipe))
        build_entry = Path(__file__).resolve().parents[3] / 'compositor/luminophore/scripts/build-canary'
        result = subprocess.run([str(build_entry), '--pack-release', str(self.sysroot),
                                 str(recipe), str(self.root / 'artifacts')],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((Path(json.loads(result.stdout)['release']) / 'release.json').is_file())

    def test_invalid_input_has_structured_failure(self):
        result = self.call('verify', '--release', self.root / 'missing')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)['status'], 'FAIL')
        self.assertNotIn('Traceback', result.stderr)

    def test_system_shell_bootstrap_preserves_environment_for_legacy_path(self):
        source = Path(__file__).resolve().parents[3] / 'config'
        snippet = "from luminophore_shell.bootstrap import configure_release; import os; configure_release(); print(os.environ['PYTHONHOME'])"
        env = {**os.environ, 'PYTHONPATH': str(source)}
        # Set after interpreter initialization, to exercise the bootstrap itself.
        result = subprocess.run([sys.executable, '-c', "import os; os.environ['PYTHONHOME']='legacy'; " + snippet],
                                env=env, text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout), (0, 'legacy\n'), result.stderr)

    def test_private_shell_bootstrap_removes_child_python_and_gi_environment(self):
        source = Path(__file__).resolve().parents[3] / 'config'
        release = self.root / 'runtime'
        (release / 'lib').mkdir(parents=True)
        (release / 'typelibs').mkdir()
        snippet = """
import os
os.environ['PYTHONHOME'] = os.environ['LUMINOPHORE_RELEASE_ROOT'] + '/python'
os.environ['GI_TYPELIB_PATH'] = os.environ['LUMINOPHORE_RELEASE_ROOT'] + '/typelibs'
from luminophore_shell.bootstrap import configure_release
configure_release()
print('PYTHONHOME' in os.environ, 'GI_TYPELIB_PATH' in os.environ)
"""
        env = {**os.environ, 'PYTHONPATH': str(source), 'LUMINOPHORE_RELEASE_ROOT': str(release)}
        result = subprocess.run([sys.executable, '-c', snippet], env=env, text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout), (0, 'False False\n'), result.stderr)

    def test_release_autostart_uses_pinned_shell_and_limited_dbus_import(self):
        script = Path(__file__).resolve().parents[3] / 'config/autostart.lua'
        harness = "hl={on=function(_,f) f() end,exec_cmd=function(cmd) print(cmd) end}; dofile(arg[1])"
        env = {**os.environ, 'LUMINOPHORE_COMPOSITOR': '1',
               'LUMINOPHORE_RELEASE_ROOT': '/test/release',
               'LUMINOPHORE_SHELL_COMMAND': 'pinned-shell-command'}
        result = subprocess.run(['/usr/bin/lua', '-e', harness, '--', script],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('pinned-shell-command', result.stdout)
        self.assertNotIn('--all', result.stdout)
        self.assertNotIn('start luminophore-session.target', result.stdout)

    def test_shell_supervisor_stops_when_its_session_owner_exits(self):
        self.cc('#include <unistd.h>\nint main(void) { for (;;) pause(); }',
                'usr/bin/demo', ['-Wl,-rpath,$ORIGIN/../lib'])
        from luminophore_runtime.state import Store
        release = self.pack()
        store = Store(self.root / 'store')
        store.stage(release, release.name)
        store.select(release.name, self.sysroot, 1, config_version=1)
        owner = subprocess.Popen(['/usr/bin/sleep', '30'])
        token = str(owner.pid) + ':' + Path(f'/proc/{owner.pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        env = {**os.environ, 'LUMINOPHORE_SESSION_OWNER': token}
        child = subprocess.Popen([sys.executable, str(CLI), 'run', '--store', str(store.root),
            '--component', 'shell', '--host-root', str(self.sysroot), '--config-version', '1'],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.3)
            owner.terminate()
            owner.wait()
            child.communicate(timeout=5)
            self.assertNotEqual(child.returncode, 0)
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait()
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)

    def test_shell_resolves_v2_control_and_rejects_tampering(self):
        source = Path(__file__).resolve().parents[3] / 'config'
        release = self.pack()
        snippet = 'from luminophore_shell.compositor_runtime import resolve_hyprctl; print(resolve_hyprctl())'
        env = {**os.environ, 'PYTHONPATH': str(source), 'LUMINOPHORE_RELEASE_ROOT': str(release),
               'LUMINOPHORE_COMPOSITOR': '1'}
        result = subprocess.run([sys.executable, '-c', snippet], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(release / 'bin/demo'))
        (release / 'bin/demo').write_bytes(b'changed')
        result = subprocess.run([sys.executable, '-c', snippet], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_internal_helper_environment_restores_only_declared_runtime(self):
        source = Path(__file__).resolve().parents[3] / 'config'
        (self.sysroot / 'python').mkdir()
        (self.sysroot / 'python/resource').write_text('fixture')
        self.recipe['files']['python'] = 'python'
        self.recipe['runtime_env']['PYTHONHOME'] = 'python'
        release = self.pack()
        env = {**os.environ, 'PYTHONPATH': str(source), 'LUMINOPHORE_RELEASE_ROOT': str(release)}
        snippet = "from luminophore_shell.bootstrap import internal_python_environment; import os; e=internal_python_environment(); print(e['PYTHONHOME']); print('PYTHONHOME' in os.environ)"
        result = subprocess.run([sys.executable, '-c', snippet], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(release / 'python') + '\nFalse\n')

    def test_locked_build_executes_in_an_isolated_root(self):
        from luminophore_runtime import build
        for name in ['src', 'build', 'proc', 'dev', 'tmp']:
            (self.sysroot / name).mkdir()
        self.cc('#include <stdio.h>\n#include <unistd.h>\nint main(void) { if(access("/etc/hostname", F_OK)==0) return 3; FILE *f=fopen("/build/proof", "w"); if(!f)return 4; fputs("isolated",f); return fclose(f); }',
                'usr/bin/demo', ['-Wl,-rpath,$ORIGIN/../lib'])
        source = self.root / 'source'
        source.mkdir()
        (source / 'input').write_text('locked')
        output = self.root / 'build-output'
        lock = build.create_lock(self.sysroot, source, ['/usr/bin/demo'], 0)
        output.mkdir()
        result = subprocess.run(build.command(self.sysroot, source, output, lock),
                                text=True, capture_output=True)
        if result.returncode and ('namespace' in result.stderr or 'Operation not permitted' in result.stderr):
            self.skipTest('nested namespace unavailable: ' + result.stderr.strip())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((output / 'proof').read_text(), 'isolated')
