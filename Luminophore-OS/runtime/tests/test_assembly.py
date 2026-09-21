import copy
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from test_desktop import DesktopFixture
from luminophore_runtime import artifact, assembly, build, desktop
from luminophore_runtime.common import ContractError, identity, load


class AssemblyTests(DesktopFixture):
    def setUp(self):
        super().setUp()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.output = self.root / 'build-output'
        self.output.mkdir()
        self.layout = {'schema': 'luminophore-build-layout/v1', 'desktop':
                       {k: copy.deepcopy(v) for k, v in self.spec.items() if k != 'packages'}, 'packages': []}
        del self.layout['desktop']['provenance']['build_lock']
        self.layout['desktop']['system_files'] = []
        for package in self.spec['packages']:
            directory = 'packages/' + package['name']
            for name in package['files']:
                path = self.output / directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.sysroot / name, path)
            self.layout['packages'].append({k: v for k, v in package.items() if k != 'files'} | {'root': directory})
        self.save_layout()

    def save_layout(self):
        (self.source / 'layout.json').write_text(json.dumps(self.layout))
        self.lock = build.create_lock(self.sysroot, self.source, ['/usr/bin/true'], 1)
        self.receipt = {'build_lock': self.lock['digest'], 'output': identity(build.tree(self.output)),
                        'reproducibility': 'requires-independent-repeat'}

    def assemble(self, target=None):
        return assembly.assemble(self.output, self.source, self.lock, self.receipt,
                                 'layout.json', target or self.root / 'assembled')

    def test_assembled_root_packages_with_generated_ownership(self):
        result = self.assemble()
        root = Path(result['sysroot'])
        spec = load(Path(result['input']))
        self.assertEqual(spec['provenance']['build_lock'], self.lock['digest'])
        self.assertEqual(spec['packages'][0]['files'], self.spec['packages'][0]['files'])
        release = desktop.pack(root, spec, self.root / 'artifacts')
        artifact.verify(release, root)
        self.assertEqual(subprocess.run([release / 'bin/Hyprland']).returncode, 0)
        self.assertEqual(load(root.parent / 'build-receipt.json'), self.receipt)
        self.assertEqual(assembly.verify(root.parent)['assembly'], result['assembly'])

    def test_changed_output_and_wrong_receipt_are_rejected(self):
        self.receipt['build_lock'] = '1' * 64
        with self.assertRaisesRegex(ContractError, 'receipt'):
            self.assemble()
        self.save_layout()
        (self.output / 'unexpected').write_text('not covered by receipt')
        with self.assertRaisesRegex(ContractError, 'output'):
            self.assemble()
        self.assertFalse((self.root / 'assembled').exists())

    def test_layout_must_be_in_locked_source(self):
        (self.source / 'layout.json').write_text('{}')
        with self.assertRaisesRegex(ContractError, 'source'):
            self.assemble()

    def test_identical_cross_package_file_collision_rejected(self):
        target = self.output / 'packages/system/usr/lib/luminophore/config/desktop/bindings.toml'
        target.parent.mkdir(parents=True)
        shutil.copy2(self.output / 'packages/private/usr/lib/luminophore/config/desktop/bindings.toml', target)
        self.save_layout()
        with self.assertRaisesRegex(ContractError, 'collision'):
            self.assemble()

    def test_package_roots_cannot_overlap_or_escape(self):
        for value in ['../outside', 'packages', 'packages/private']:
            with self.subTest(value=value):
                self.layout['packages'][1]['root'] = value
                self.save_layout()
                with self.assertRaises(ContractError):
                    self.assemble()

    def test_external_symlink_rejected_and_internal_link_materialized(self):
        directory = self.output / 'packages/private/usr/lib/luminophore/config'
        (directory / 'alias.toml').symlink_to('/etc/hostname')
        self.save_layout()
        with self.assertRaises(ContractError):
            self.assemble()
        (directory / 'alias.toml').unlink()
        (directory / 'alias.toml').symlink_to('desktop/bindings.toml')
        self.save_layout()
        result = self.assemble()
        self.assertFalse((Path(result['sysroot']) / 'usr/lib/luminophore/config/alias.toml').is_symlink())

    def test_failed_publication_and_existing_output_are_preserved(self):
        with patch.object(assembly.os, 'rename', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.assemble()
        self.assertFalse((self.root / 'assembled').exists())
        self.assemble()
        with self.assertRaisesRegex(ContractError, 'exists'):
            self.assemble()

    def test_tampered_assembled_input_is_detected(self):
        result = self.assemble()
        Path(result['input']).write_text('{}')
        with self.assertRaisesRegex(ContractError, 'digest'):
            assembly.verify(Path(result['sysroot']).parent)

    def test_no_output_can_overlap_inputs(self):
        with self.assertRaisesRegex(ContractError, 'overlap'):
            self.assemble(self.output / 'assembly')

    def test_cli_assembles_and_verifies(self):
        for name, value in [('lock', self.lock), ('receipt', self.receipt)]:
            (self.root / (name + '.json')).write_text(json.dumps(value))
        cli = Path(__file__).resolve().parents[1] / 'luminophore-runtime'
        result = subprocess.run([str(cli), 'assemble-desktop', '--source', str(self.source),
                                 '--build-output', str(self.output), '--lock', str(self.root / 'lock.json'),
                                 '--receipt', str(self.root / 'receipt.json'), '--layout', 'layout.json',
                                 '--output', str(self.root / 'assembled')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = subprocess.run([str(cli), 'verify-assembly', '--assembly', str(self.root / 'assembled')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pack_assembly_preserves_build_evidence(self):
        self.assemble()
        release = assembly.pack(self.root / 'assembled', self.root / 'artifacts')
        evidence = load(release / 'metadata/build-assembly.json')
        self.assertEqual(evidence['receipt'], self.receipt)
        self.assertEqual(evidence['assembly']['build_lock'], self.lock['digest'])
        artifact.verify(release)

    def test_mutation_during_copy_cannot_publish(self):
        original = assembly.shutil.copyfile
        def changed(src, target):
            result = original(src, target)
            if str(src).endswith('bindings.toml'):
                Path(src).write_text('changed')
            return result
        with patch.object(assembly.shutil, 'copyfile', side_effect=changed):
            with self.assertRaisesRegex(ContractError, 'output'):
                self.assemble()
        self.assertFalse((self.root / 'assembled').exists())

    def test_real_isolated_build_flows_into_assembly_and_pack(self):
        # A real ELF builder copies fixture package roots from locked /src.
        # It cannot read a host /etc/hostname. No mocked build.run or bwrap.
        self.cc(r'''
#define _XOPEN_SOURCE 700
#include <ftw.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static int copy_one(const char *p, const struct stat *s, int type, struct FTW *f) {
    char out[4096]; (void)f;
    if(snprintf(out,sizeof out,"/build/%s",p+5)>=(int)sizeof out)return 1;
    if(type==FTW_D)return mkdir(out,s->st_mode&0777);
    if(type!=FTW_F)return 2;
    FILE *a=fopen(p,"rb"), *b=fopen(out,"wb"); if(!a||!b)return 3;
    char buf[8192]; size_t n; while((n=fread(buf,1,sizeof buf,a)))if(fwrite(buf,1,n,b)!=n)return 4;
    if(ferror(a))return 5;
    if(fclose(a)||fclose(b))return 6;
    return chmod(out,s->st_mode&0777);
}
int main(void) {
    puts("builder progress");
    if(access("/etc/hostname",F_OK)==0)return 7;
    return nftw("/src/packages",copy_one,16,FTW_PHYS);
}
''', 'usr/bin/builder', [])
        for name in ['src', 'build', 'proc', 'dev', 'tmp']:
            (self.sysroot / name).mkdir(exist_ok=True)
        shutil.copytree(self.output / 'packages', self.source / 'packages')
        lock = build.create_lock(self.sysroot, self.source, ['/usr/bin/builder'], 1)
        lock_path = self.root / 'actual-lock.json'
        lock_path.write_text(json.dumps(lock))
        cli = Path(__file__).resolve().parents[1] / 'luminophore-runtime'
        process = subprocess.run([str(cli), 'build-desktop', '--sysroot', str(self.sysroot),
                                  '--source', str(self.source), '--lock', str(lock_path),
                                  '--layout', 'layout.json', '--build-output', str(self.root / 'actual-build'),
                                  '--output', str(self.root / 'actual-assembly')], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertIn('builder progress', process.stderr)
        release = assembly.pack(Path(result['sysroot']).parent, self.root / 'actual-artifacts')
        self.assertEqual(subprocess.run([release / 'bin/Hyprland']).returncode, 0)

    def test_changed_input_after_assembly_verification_cannot_publish(self):
        self.assemble()
        original = assembly.verify
        def changed(path):
            result = original(path)
            spec_path = Path(path) / 'desktop-input.json'
            spec = load(spec_path)
            spec['provenance']['source'] = 'changed after verification'
            spec_path.write_text(json.dumps(spec))
            return result
        with patch.object(assembly, 'verify', side_effect=changed):
            with self.assertRaisesRegex(ContractError, 'digest'):
                assembly.pack(self.root / 'assembled', self.root / 'artifacts')
        self.assertFalse((self.root / 'artifacts').exists())

    def test_changed_file_mode_after_verification_cannot_publish(self):
        self.assemble()
        original = assembly.verify
        def changed(path):
            result = original(path)
            (Path(path) / 'root/usr/lib/luminophore/config/desktop/bindings.toml').chmod(0o755)
            return result
        with patch.object(assembly, 'verify', side_effect=changed):
            with self.assertRaisesRegex(ContractError, 'mode changed'):
                assembly.pack(self.root / 'assembled', self.root / 'artifacts')
        self.assertFalse(any((self.root / 'artifacts').glob('[0-9a-f]' * 64)))
