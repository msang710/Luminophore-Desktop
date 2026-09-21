import json
from pathlib import Path
import tempfile
import unittest

from luminophore_runtime import build, managed
from luminophore_runtime.common import ContractError, identity


class BuildAndManagedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysroot = self.root / 'sysroot'
        self.source = self.root / 'source'
        self.output = self.root / 'output'
        self.sysroot.mkdir()
        self.source.mkdir()
        (self.sysroot / 'toolchain').write_text('locked compiler')
        (self.source / 'build.sh').write_text('exit 0')

    def test_build_lock_catches_added_and_changed_inputs(self):
        lock = build.create_lock(self.sysroot, self.source, ['sh', '/src/build.sh'], 1700000000)
        build.verify_lock(self.sysroot, self.source, lock)
        (self.source / 'new.h').write_text('changed build input')
        with self.assertRaisesRegex(ContractError, 'source'):
            build.verify_lock(self.sysroot, self.source, lock)
        (self.source / 'new.h').unlink()
        (self.sysroot / 'toolchain').write_text('updated compiler')
        with self.assertRaisesRegex(ContractError, 'sysroot'):
            build.verify_lock(self.sysroot, self.source, lock)

    def test_locked_build_command_has_only_declared_mounts_and_no_network(self):
        lock = build.create_lock(self.sysroot, self.source, ['sh', '/src/build.sh'], 1700000000)
        command = build.command(self.sysroot, self.source, self.output, lock)
        self.assertIn('--unshare-net', command)
        self.assertIn('--clearenv', command)
        binds = [command[i + 1:i + 3] for i, word in enumerate(command) if word in {'--ro-bind', '--bind'}]
        self.assertEqual(binds, [[str(self.sysroot), '/'], [str(self.source), '/src'], [str(self.output), '/build']])
        self.assertEqual(command[-2:], ['sh', '/src/build.sh'])

    def test_output_inside_source_or_sysroot_rejected(self):
        lock = build.create_lock(self.sysroot, self.source, ['sh'], 0)
        for out in [self.source / 'out', self.sysroot / 'out', self.root]:
            with self.subTest(out=out), self.assertRaises(ContractError):
                build.command(self.sysroot, self.source, out, lock)

    def candidate(self):
        root = self.root / 'candidate'
        boot = self.root / 'boot'
        root.mkdir()
        boot.mkdir()
        for relative in ['usr/lib/modules/fixture/kernel.ko', 'var/lib/pacman/local/pkg/desc', 'etc/os-release']:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('candidate')
        for name in ['kernel', 'initramfs', 'entry.conf']:
            (boot / name).write_text('fixture-' + name)
        release = 'a' * 64
        descriptor = managed.describe(root, boot, release, 'b' * 64,
            {'kernel': 'kernel', 'initramfs': 'initramfs', 'entry': 'entry.conf'})
        return root, boot, descriptor

    def test_candidate_identity_binds_root_boot_de_and_profile(self):
        root, boot, descriptor = self.candidate()
        self.assertEqual(managed.verify(root, boot, descriptor), descriptor['deployment'])
        (boot / 'kernel').write_text('different kernel')
        with self.assertRaisesRegex(ContractError, 'boot'):
            managed.verify(root, boot, descriptor)

    def test_candidate_detects_partial_package_database_change(self):
        root, boot, descriptor = self.candidate()
        (root / 'var/lib/pacman/local/pkg/desc').write_text('new package database')
        with self.assertRaisesRegex(ContractError, 'root'):
            managed.verify(root, boot, descriptor)

    def test_validation_receipts_do_not_promote_wrong_or_unverified_hardware(self):
        root, boot, descriptor = self.candidate()
        with self.assertRaisesRegex(ContractError, 'evidence'):
            managed.admit(root, boot, descriptor, [], 'confirmed')
        checks = {key: True for key in managed.CHECKS}
        vm = {'deployment': descriptor['deployment'], 'boundary': 'vm', 'checks': checks}
        self.assertEqual(managed.admit(root, boot, descriptor, [vm], 'trial')['status'], 'trial')
        with self.assertRaises(ContractError):
            managed.admit(root, boot, descriptor, [vm], 'confirmed')
        wrong = {'deployment': 'c' * 64, 'boundary': 'physical', 'checks': checks}
        with self.assertRaises(ContractError):
            managed.admit(root, boot, descriptor, [vm, wrong], 'confirmed')

    def test_live_root_and_missing_boot_assets_refused(self):
        root, boot, descriptor = self.candidate()
        with self.assertRaises(ContractError):
            managed.describe(Path('/'), boot, 'a' * 64, 'b' * 64,
                {'kernel': 'kernel', 'initramfs': 'initramfs', 'entry': 'entry.conf'})
        (boot / 'initramfs').unlink()
        with self.assertRaises((OSError, ContractError)):
            managed.verify(root, boot, descriptor)

    def test_config_copy_never_mutates_shared_data_or_reuses_incompatible_version(self):
        source = self.root / 'settings'
        source.mkdir()
        (source / 'settings.toml').write_text('value = 1')
        target = managed.copy_config(source, self.root / 'state', 'a' * 64, 1)
        (target / 'settings.toml').write_text('value = 2')
        self.assertEqual((source / 'settings.toml').read_text(), 'value = 1')
        with self.assertRaisesRegex(ContractError, 'already'):
            managed.copy_config(source, self.root / 'state', 'a' * 64, 2)

