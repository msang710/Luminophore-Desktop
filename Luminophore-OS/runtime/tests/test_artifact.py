import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luminophore_runtime import artifact, inventory
from luminophore_runtime.common import ContractError, digest


class ArtifactFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysroot = self.root / 'sysroot'
        (self.sysroot / 'usr/lib').mkdir(parents=True)
        (self.sysroot / 'usr/bin').mkdir()
        self.cc('int leaf(void) { return 42; }', 'usr/lib/libleaf.so.1',
                ['-shared', '-fPIC', '-Wl,-soname,libleaf.so.1', '-Wl,-rpath,$ORIGIN'])
        self.cc('extern int leaf(void); int answer(void) { return leaf(); }',
                'usr/lib/libanswer.so.1', ['-shared', '-fPIC', '-Wl,-soname,libanswer.so.1',
                '-Wl,-rpath,$ORIGIN', '-L' + str(self.sysroot / 'usr/lib'), '-l:libleaf.so.1'])
        self.cc('#include <stdio.h>\nextern int answer(void); int main(void) {printf("%d\\n",answer());}',
                'usr/bin/demo', ['-L' + str(self.sysroot / 'usr/lib'), '-l:libanswer.so.1',
                '-Wl,-rpath-link,' + str(self.sysroot / 'usr/lib'), '-Wl,-rpath,$ORIGIN/../lib'])
        self.providers = {}
        # Derive the platform loader location from the built ELF, not a distro assumption.
        info = subprocess.check_output(['/usr/bin/readelf', '-lW', self.sysroot / 'usr/bin/demo'], text=True)
        loader = info.split('Requesting program interpreter: ', 1)[1].split(']', 1)[0]
        self.loader = loader
        for source in [Path(loader).resolve(), Path(loader), Path('/usr/lib/libc.so.6')]:
            target = self.sysroot / str(source).lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
            self.providers[source.name] = {'path': str(source).lstrip('/'), 'owner': 'system'}
        for name in ['libanswer.so.1', 'libleaf.so.1']:
            self.providers[name] = {'path': 'usr/lib/' + name, 'owner': 'private'}
        self.recipe = {'schema': 1, 'providers': self.providers,
            'files': {'bin/demo': 'usr/bin/demo'}, 'dynamic': [],
            'entries': {name: {'path': 'bin/demo', 'args': []} for name in ['compositor', 'control', 'shell']},
            'compatibility': {'config': 1, 'ipc': 1},
            'provenance': {'source': 'fixture', 'build_lock': '0' * 64, 'license': 'test-only'},
            'system_files': {}, 'runtime_env': {}}

    def cc(self, source, target, flags):
        path = self.sysroot / target
        subprocess.run(['/usr/bin/cc', '-x', 'c', '-', '-o', str(path), *flags],
                       input=source, text=True, capture_output=True, check=True)

    def pack(self):
        return artifact.pack(self.sysroot, self.recipe, self.root / 'artifacts')


class ArtifactTests(ArtifactFixture):
    def test_preflight_hashes_unchanged_shared_provider_once(self):
        release = self.pack()
        provider = (release / 'lib/libleaf.so.1').resolve()
        with patch.object(artifact, 'digest', wraps=digest) as calls:
            artifact.preflight(release)
        self.assertEqual(sum(Path(call.args[0]).resolve() == provider
                             for call in calls.call_args_list), 1)

    def test_preflight_rechecks_provider_changed_between_loader_invocations(self):
        release = self.pack()
        provider = release / 'lib/libleaf.so.1'
        run = subprocess.run
        count = 0
        def changing_loader(*args, **kwargs):
            nonlocal count
            result = run(*args, **kwargs)
            count += 1
            if count == 2:
                previous = provider.stat()
                provider.chmod(0o644)
                data = provider.read_bytes()
                provider.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                # Same length and restored mtime must not reuse a stale digest.
                os.utime(provider, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            return result
        with patch.object(artifact.subprocess, 'run', side_effect=changing_loader):
            with self.assertRaisesRegex(ContractError, 'unpinned provider'):
                artifact.preflight(release)

    def test_old_relocator_is_rejected_before_publishing(self):
        patcher = self.root / 'old-patchelf'
        patcher.write_text('#!/bin/sh\necho "patchelf 0.17.2"\n')
        patcher.chmod(0o755)
        with self.assertRaisesRegex(ContractError, 'patchelf'):
            artifact.pack(self.sysroot, self.recipe, self.root / 'old-output', patchelf=patcher)
        self.assertFalse((self.root / 'old-output').exists())

    def test_transitive_libraries_survive_removed_build_root(self):
        release = self.pack()
        self.assertTrue((release / 'lib/libleaf.so.1').is_file())
        manifest = artifact.verify(release, self.sysroot)
        self.assertEqual(manifest['entries']['control']['path'], 'bin/demo')
        shutil.rmtree(self.sysroot)
        env = {k: v for k, v in os.environ.items() if not k.startswith('LD_')}
        self.assertEqual(subprocess.check_output([release / 'bin/demo'], env=env, text=True), '42\n')

    def test_missing_transitive_provider_refuses_before_publish(self):
        del self.recipe['providers']['libleaf.so.1']
        with self.assertRaisesRegex(ContractError, 'provider'):
            self.pack()
        self.assertFalse((self.root / 'artifacts').exists())

    def test_dynamic_library_is_copied_even_without_needed_edge(self):
        self.cc('int plugin(void) { return 7; }', 'usr/lib/libplugin.so',
                ['-shared', '-fPIC', '-Wl,-rpath,$ORIGIN'])
        self.recipe['dynamic'] = ['usr/lib/libplugin.so']
        release = self.pack()
        self.assertTrue((release / 'lib/libplugin.so').is_file())

    def test_host_profile_drift_and_artifact_tampering_are_distinct(self):
        release = self.pack()
        (self.sysroot / 'usr/lib/libc.so.6').write_bytes(b'new host libc')
        with self.assertRaisesRegex(ContractError, 'host compatibility'):
            artifact.verify(release, self.sysroot)
        (release / 'lib/libleaf.so.1').write_bytes(b'tampered')
        with self.assertRaisesRegex(ContractError, 'digest'):
            artifact.verify(release)

    def test_generation_includes_resources_and_metadata(self):
        first = self.pack()
        self.recipe['compatibility']['config'] = 2
        second = self.pack()
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(second, self.pack())

    def test_escaping_paths_and_links_rejected(self):
        for target in ['../escape', '/absolute', 'bin/../../escape']:
            with self.subTest(target=target):
                bad = copy.deepcopy(self.recipe)
                bad['files'] = {target: 'usr/bin/demo'}
                with self.assertRaises(ContractError):
                    artifact.pack(self.sysroot, bad, self.root / 'artifacts')
        (self.sysroot / 'usr/bin/link').symlink_to('/etc/passwd')
        self.recipe['files']['etc/leak'] = 'usr/bin/link'
        with self.assertRaises(ContractError):
            self.pack()

    def test_extra_file_and_manifest_mutation_rejected(self):
        release = self.pack()
        (release / 'bin/extra').write_text('untracked')
        with self.assertRaisesRegex(ContractError, 'file set'):
            artifact.verify(release)
        (release / 'bin/extra').unlink()
        path = release / 'release.json'
        manifest = json.loads(path.read_text())
        manifest['entries']['shell']['args'] = ['--changed']
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ContractError, 'generation'):
            artifact.verify(release)

    def test_host_library_cannot_depend_on_a_private_provider(self):
        self.recipe['providers']['libanswer.so.1']['owner'] = 'system'
        with self.assertRaisesRegex(ContractError, 'system.*private'):
            self.pack()

    def test_profile_includes_interpreter_and_symbol_requirements(self):
        release = self.pack()
        manifest = artifact.verify(release)
        self.assertIn(self.loader.lstrip('/'), manifest['system_files'])
        self.assertEqual(manifest['host_contract']['schema'], 'luminophore-host-contract/v1')
        self.assertIn('GLIBC_', json.dumps(manifest['elf']))

    def test_pinned_system_library_must_be_found_by_actual_loader(self):
        self.recipe['providers']['libleaf.so.1']['owner'] = 'system'
        release = self.pack()
        with self.assertRaisesRegex(ContractError, 'provider-missing'):
            artifact.preflight(release)
        # Fixture root supplies the provider, but its real loader must find it.
        with self.assertRaisesRegex(ContractError, 'loader'):
            artifact.preflight(release, self.sysroot)

    def test_loader_preflight_resolves_private_closure(self):
        release = self.pack()
        artifact.preflight(release)


if __name__ == '__main__':
    unittest.main()
