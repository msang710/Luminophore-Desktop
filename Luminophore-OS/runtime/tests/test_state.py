import copy
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from test_artifact import ArtifactFixture
from luminophore_runtime import artifact
from luminophore_runtime.common import ContractError
from luminophore_runtime.state import Store
from luminophore_runtime.session import application_environment


class StoreTests(ArtifactFixture):
    def setUp(self):
        super().setUp()
        self.store = Store(self.root / 'store')

    def add(self):
        release = self.pack()
        self.store.stage(release, release.name)
        return release.name

    def select(self, generation):
        state = self.store.read()
        return self.store.select(generation, self.sysroot, state['revision'], config_version=1)

    def test_selection_rollback_and_cas(self):
        first = self.add()
        self.select(first)
        self.recipe['provenance']['source'] = 'second'
        second = self.add()
        old = self.store.read()['revision']
        self.select(second)
        with self.assertRaisesRegex(ContractError, 'STALE'):
            self.store.select(first, self.sysroot, old, config_version=1)
        self.store.rollback(self.sysroot, self.store.read()['revision'], config_version=1)
        state = self.store.read()
        self.assertEqual((state['selected'], state['previous']), (first, second))

    def test_interrupted_state_write_retains_previous_selection(self):
        first = self.add()
        self.select(first)
        self.recipe['provenance']['source'] = 'second'
        second = self.add()
        before = self.store.read()
        with patch('luminophore_runtime.common.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.select(second)
        self.assertEqual(self.store.read(), before)

    def test_session_pins_old_generation_and_gc_waits_for_lease(self):
        first = self.add()
        self.select(first)
        with self.store.session(self.sysroot, config_version=1) as session:
            for label in ['second', 'third']:
                self.recipe['provenance']['source'] = label
                self.select(self.add())
            command, env = session.command('shell')
            self.assertEqual(Path(command[0]).parents[1].name, first)
            self.assertEqual(subprocess.check_output(command, env=env, text=True), '42\n')
            self.assertNotIn(first, self.store.gc())
            self.assertTrue((self.store.generations / first).is_dir())
        self.assertIn(first, self.store.gc())

    def test_child_inherits_lease_after_supervisor_exits(self):
        first = self.add()
        self.select(first)
        with self.store.session(self.sysroot, config_version=1) as session:
            child = subprocess.Popen(['/usr/bin/sleep', '20'], pass_fds=(session.lease_fd,))
            self.addCleanup(lambda: child.poll() is None and child.kill())
            self.addCleanup(child.wait)
            for label in ['second', 'third']:
                self.recipe['provenance']['source'] = label
                self.select(self.add())
        self.assertNotIn(first, self.store.gc())
        child.terminate()
        child.wait()
        self.assertIn(first, self.store.gc())

    def test_host_drift_refuses_selection_and_new_session(self):
        first = self.add()
        self.select(first)
        (self.sysroot / 'usr/lib/libc.so.6').write_bytes(b'changed')
        with self.assertRaisesRegex(ContractError, 'host'):
            self.select(first)
        with self.assertRaisesRegex(ContractError, 'host'):
            with self.store.session(self.sysroot, config_version=1):
                self.fail('must not enter stale session')

    def test_compatibility_inventory_does_not_remove_generation(self):
        first = self.add()

        result = self.store.compatibility_inventory(self.sysroot)

        self.assertEqual(result[first]['status'], 'compatible')
        self.assertTrue((self.store.generations / first).is_dir())

    def test_revoked_release_remains_for_running_session_but_not_selected(self):
        first = self.add()
        self.select(first)
        with self.store.session(self.sysroot, config_version=1):
            self.store.revoke(first, self.store.read()['revision'])
            with self.assertRaisesRegex(ContractError, 'revoked'):
                self.select(first)
            self.assertNotIn(first, self.store.gc())

    def test_incompatible_config_refuses_selection(self):
        first = self.add()
        with self.assertRaisesRegex(ContractError, 'config'):
            self.store.select(first, self.sysroot, self.store.read()['revision'], config_version=2)

    def test_app_environment_does_not_inherit_runtime_search_paths(self):
        env = application_environment({'PATH': '/private/bin:/usr/bin', 'HOME': '/home/test',
            'WAYLAND_DISPLAY': 'wayland-1', 'LD_LIBRARY_PATH': '/private/lib',
            'LD_PRELOAD': 'evil.so', 'PYTHONHOME': '/private/python', 'PYTHONPATH': 'bad',
            'FONTCONFIG_FILE': '/private/fonts.conf', 'FONTCONFIG_PATH': '/private/fonts',
            'QT_QPA_PLATFORM_PLUGIN_PATH': '/old/qt', 'QML_IMPORT_PATH': '/old/qml',
            'GI_TYPELIB_PATH': '/private/typelib', 'GIO_MODULE_DIR': '/private/gio',
            'GSETTINGS_SCHEMA_DIR': '/private/schema', 'LUMINOPHORE_RELEASE_ROOT': '/private'})
        self.assertEqual(env, {'PATH': '/usr/bin:/bin', 'HOME': '/home/test', 'WAYLAND_DISPLAY': 'wayland-1'})

    def test_wrong_trusted_digest_rejected(self):
        release = self.pack()
        with self.assertRaises(ContractError):
            self.store.stage(release, 'f' * 64)
        self.assertIsNone(self.store.read()['candidate'])

    def test_compositor_starts_with_own_identity_and_no_previous_instance(self):
        self.select(self.add())
        with self.store.session(self.sysroot, config_version=1) as session:
            _, env = session.command('compositor', environment={
                'XDG_CURRENT_DESKTOP': 'Hyprland', 'XDG_SESSION_DESKTOP': 'Hyprland',
                'HYPRLAND_INSTANCE_SIGNATURE': 'old_instance',
                'LUMINOPHORE_INSTANCE_SIGNATURE': 'old_luminophore',
            })
            self.assertEqual(env['XDG_CURRENT_DESKTOP'], 'Luminophore')
            self.assertEqual(env['XDG_SESSION_DESKTOP'], 'luminophore')
            self.assertNotIn('HYPRLAND_INSTANCE_SIGNATURE', env)

    def test_control_preserves_current_luminophore_instance(self):
        self.select(self.add())
        with self.store.session(self.sysroot, config_version=1) as session:
            _, env = session.command('control', environment={
                'LUMINOPHORE_INSTANCE_SIGNATURE': 'current_instance',
                'HYPRLAND_INSTANCE_SIGNATURE': 'stale_instance',
            })
            self.assertEqual(env.get('LUMINOPHORE_INSTANCE_SIGNATURE'), 'current_instance')
            self.assertNotIn('HYPRLAND_INSTANCE_SIGNATURE', env)

    def test_unconfirmed_candidate_does_not_become_known_good(self):
        first = self.add()
        self.select(first)
        self.assertIsNone(self.store.read()['confirmed'])
        with self.assertRaisesRegex(ContractError, 'health'):
            self.store.confirm(first, {'generation': first, 'checks': {}}, self.store.read()['revision'], self.sysroot)

    def test_confirmation_rechecks_host_and_selection_revision(self):
        from luminophore_runtime.common import identity
        from luminophore_runtime.state import HEALTH_CHECKS
        first = self.add()
        state = self.select(first)
        manifest = artifact.verify(self.store.generations / first)
        receipt = {'schema': 1, 'generation': first, 'profile': identity(manifest['host_contract']),
            'boundary': 'physical', 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'selection_revision': state['revision'], 'checks': {key: True for key in HEALTH_CHECKS}}
        (self.sysroot / 'usr/lib/libc.so.6').write_bytes(b'changed')
        with self.assertRaisesRegex(ContractError, 'host'):
            self.store.confirm(first, receipt, state['revision'], self.sysroot)
        self.assertIsNone(self.store.read()['confirmed'])

    def test_same_boot_health_cannot_confirm_later_selection(self):
        from luminophore_runtime.common import identity
        from luminophore_runtime.state import HEALTH_CHECKS
        first = self.add()
        state = self.select(first)
        manifest = artifact.verify(self.store.generations / first)
        receipt = {'schema': 1, 'generation': first, 'profile': identity(manifest['host_contract']),
            'boundary': 'physical', 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'selection_revision': state['revision'], 'checks': {key: True for key in HEALTH_CHECKS}}
        state = self.select(first)
        with self.assertRaisesRegex(ContractError, 'health'):
            self.store.confirm(first, receipt, state['revision'], self.sysroot)
