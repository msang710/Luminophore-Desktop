import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from test_artifact import ArtifactFixture
from luminophore_runtime import artifact, desktop
from luminophore_runtime.common import ContractError, digest


class DesktopFixture(ArtifactFixture):
    def setUp(self):
        super().setUp()
        self.prefix = self.sysroot / 'usr/lib/luminophore'
        for name in ['bin/Hyprland', 'bin/hyprctl', 'bin/start-hyprland', 'python/bin/python3',
                     'bin/xdg-desktop-portal-luminophore', 'bin/luminophore-share-picker']:
            path = self.prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            relative_lib = os.path.relpath(self.prefix / 'lib', path.parent)
            self.cc('int main(void) { return 0; }', path.relative_to(self.sysroot).as_posix(),
                    ['-Wl,-rpath,$ORIGIN/' + relative_lib])
        resources = [
            'etc/fonts/fonts.conf', 'share/fonts/luminophore/fallback.ttf',
            'shell/luminophore-shell', 'shell/luminophore_shell/__main__.py',
            'shell/luminophore_shell/bootstrap.py', 'shell/luminophore_shell/settings_bootstrap.py',
            *(f'config/{profile}/{name}.toml' for profile in ('desktop', 'greeter')
              for name in ('settings', 'monitors', 'bindings', 'placement', 'bundles')),
            'config/greeter-theme.json',
            'shell/native/luminophore_glow_shader.h',
            'python/lib/python3.14/encodings/__init__.py',
            'python/lib/python3.14/site-packages/gi/__init__.py',
            'python/lib/python3.14/site-packages/PIL/__init__.py',
            'python/lib/python3.14/site-packages/requests/__init__.py',
            'python/lib/python3.14/site-packages/cairo/__init__.py',
            'python/lib/python3.14/site-packages/dbus/__init__.py',
            'python/lib/python3.14/site-packages/numpy/__init__.py',
            'python/lib/python3.14/site-packages/cv2/__init__.py',
            'python/lib/python3.14/site-packages/OpenGL/__init__.py',
            'lib/girepository-1.0/Gtk-4.0.typelib',
            'lib/girepository-1.0/Gtk4LayerShell-1.0.typelib',
            'share/glib-2.0/schemas/gschemas.compiled',
            'shell/luminophore_shell/assets/fixture.txt',
            'shell/luminophore_shell/templates/fixture.txt', 'share/licenses/desktop/LICENSE',
        ]
        for name in resources:
            path = self.prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture only\n')
        (self.prefix / 'lib/gio/modules').mkdir(parents=True)
        for name in ['lib/libgtk4-layer-shell.so.0',
                     'lib/qt6/plugins/platforms/libqwayland.so',
                     'python/lib/python3.14/site-packages/gi/_gi.so',
                     'python/lib/python3.14/site-packages/cairo/_cairo.so',
                     'python/lib/python3.14/site-packages/_dbus_bindings.so',
                     'python/lib/python3.14/site-packages/numpy/_core/_multiarray_umath.so',
                     'python/lib/python3.14/site-packages/cv2/cv2.abi3.so',
                     'lib/gio/modules/libfixture.so']:
            path = self.prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            rel = os.path.relpath(self.prefix / 'lib', path.parent)
            self.cc('int fixture(void) { return 0; }', path.relative_to(self.sysroot).as_posix(),
                    ['-shared', '-fPIC', '-Wl,-rpath,$ORIGIN' + ('/' + rel if rel != '.' else '')])
        self.spec = {'schema': 'luminophore-desktop-input/v1', 'python_version': '3.14',
                     **{key: copy.deepcopy(self.recipe[key]) for key in
                        ['providers', 'compatibility', 'provenance', 'system_files']},
                     'dynamic': [], 'packages': []}
        self.refresh_packages()

    def refresh_packages(self):
        groups = {'private': {}, 'system': {}}
        for path in self.sysroot.rglob('*'):
            if path.is_file():
                name = path.relative_to(self.sysroot).as_posix()
                owner = 'system' if name in {self.loader.lstrip('/'), 'usr/lib/libc.so.6',
                                             str(Path(self.loader).resolve()).lstrip('/')} else 'private'
                groups[owner][name] = digest(path)
        self.spec['packages'] = [{'name': owner, 'version': 'fixture-1', 'owner': owner,
                                  'license': 'fixture-only', 'license_files':
                                  ['usr/lib/luminophore/share/licenses/desktop/LICENSE'] if owner == 'private' else [],
                                  'files': values} for owner, values in groups.items()]


class DesktopTests(DesktopFixture):
    def test_missing_runtime_shader_source_is_rejected(self):
        (self.prefix / 'shell/native/luminophore_glow_shader.h').unlink()
        self.refresh_packages()
        with self.assertRaises((ContractError, FileNotFoundError)):
            desktop.recipe(self.sysroot, self.spec)

    def test_missing_spatial_editor_opengl_is_rejected(self):
        (self.prefix / 'python/lib/python3.14/site-packages/OpenGL/__init__.py').unlink()
        self.refresh_packages()
        with self.assertRaises((ContractError, FileNotFoundError)):
            desktop.recipe(self.sysroot, self.spec)

    def test_missing_shell_python_dependency_is_rejected(self):
        path = self.prefix / 'python/lib/python3.14/site-packages/cairo/__init__.py'
        path.unlink(missing_ok=True)
        self.refresh_packages()
        with self.assertRaises((ContractError, FileNotFoundError)):
            desktop.recipe(self.sysroot, self.spec)

    def test_desktop_recipe_and_pack_preserve_inventory(self):
        recipe = desktop.recipe(self.sysroot, self.spec)
        self.assertEqual(recipe['entries']['compositor']['path'], 'bin/start-hyprland')
        self.assertEqual(recipe['entries']['compositor']['args'],
            ['--path', '{release}/bin/Hyprland', '--no-nixgl', '--', '--config', '{release}/config/desktop/settings.toml'])
        self.assertEqual(recipe['entries']['shell']['path'], 'python/bin/python3')
        self.assertEqual(recipe['entries']['portal']['path'], 'bin/xdg-desktop-portal-luminophore')
        self.assertEqual(recipe['runtime_env']['FONTCONFIG_FILE'], 'etc/fonts/fonts.conf')
        self.assertEqual(recipe['runtime_env']['QT_PLUGIN_PATH'], 'lib/qt6/plugins')
        release = desktop.pack(self.sysroot, self.spec, self.root / 'desktop')
        manifest = artifact.verify(release, self.sysroot)
        self.assertIn('metadata/desktop-inventory.json', manifest['files'])
        inventory = json.loads((release / 'metadata/desktop-inventory.json').read_text())
        self.assertEqual(inventory['input'], self.spec)
        self.assertEqual(inventory['acceptance'], 'NOT_RUN')
        self.assertIn('python/lib/python3.14/site-packages/gi/_gi.so', manifest['files'])
        self.assertEqual(subprocess.run([release / 'bin/Hyprland']).returncode, 0)

    def test_missing_required_resources_are_rejected(self):
        for name in ['config/desktop/settings.toml', 'config/greeter/settings.toml',
                     'lib/girepository-1.0/Gtk-4.0.typelib',
                     'python/lib/python3.14/encodings/__init__.py']:
            with self.subTest(name=name):
                path = self.prefix / name
                content = path.read_bytes()
                path.unlink()
                with self.assertRaises((ContractError, FileNotFoundError)):
                    desktop.pack(self.sysroot, self.spec, self.root / 'desktop')
                path.write_bytes(content)
                self.assertFalse((self.root / 'desktop').exists())

    def test_missing_and_duplicate_ownership_are_rejected(self):
        name = 'usr/lib/luminophore/bin/Hyprland'
        self.spec['packages'][0]['files'].pop(name)
        with self.assertRaisesRegex(ContractError, 'ownership'):
            desktop.recipe(self.sysroot, self.spec)
        self.refresh_packages()
        self.spec['packages'][1]['files'][name] = digest(self.prefix / 'bin/Hyprland')
        with self.assertRaisesRegex(ContractError, 'ownership'):
            desktop.recipe(self.sysroot, self.spec)

    def test_system_library_cannot_be_bundled_as_private(self):
        self.spec['packages'][0]['owner'] = 'system'
        with self.assertRaisesRegex(ContractError, 'owner'):
            desktop.recipe(self.sysroot, self.spec)

    def test_modified_or_missing_license_is_rejected(self):
        (self.prefix / 'share/licenses/desktop/LICENSE').write_text('changed')
        with self.assertRaisesRegex(ContractError, 'digest'):
            desktop.recipe(self.sysroot, self.spec)
        self.refresh_packages()
        self.spec['packages'][0]['license_files'] = []
        with self.assertRaisesRegex(ContractError, 'license'):
            desktop.recipe(self.sysroot, self.spec)

    def test_dynamic_plugins_and_host_files_require_ownership(self):
        self.spec['system_files']['usr/bin/demo'] = digest(self.sysroot / 'usr/bin/demo')
        with self.assertRaisesRegex(ContractError, 'owner'):
            desktop.recipe(self.sysroot, self.spec)

    def test_drift_between_validation_and_copy_never_publishes(self):
        original = artifact.pack
        def changed(*args, **kwargs):
            (self.prefix / 'config/desktop/bindings.toml').write_text('changed after validation')
            return original(*args, **kwargs)
        with patch.object(artifact, 'pack', side_effect=changed):
            with self.assertRaisesRegex(ContractError, 'source.*changed'):
                desktop.pack(self.sysroot, self.spec, self.root / 'desktop')
        self.assertFalse(any((self.root / 'desktop').glob('[0-9a-f]' * 64)))

    def test_system_drift_between_validation_and_pack_is_rejected(self):
        original = artifact.pack
        def changed(*args, **kwargs):
            with (self.sysroot / 'usr/lib/libc.so.6').open('ab') as stream:
                stream.write(b'changed after validation')
            return original(*args, **kwargs)
        with patch.object(artifact, 'pack', side_effect=changed):
            with self.assertRaisesRegex(ContractError, 'source.*changed'):
                desktop.pack(self.sysroot, self.spec, self.root / 'desktop')

    def test_external_symlink_never_uses_host_resource(self):
        path = self.prefix / 'config/desktop/settings.toml'
        path.unlink()
        path.symlink_to('/etc/hostname')
        with self.assertRaisesRegex(ContractError, 'escapes'):
            desktop.recipe(self.sysroot, self.spec)

    def test_unknown_input_and_python_versions_fail_closed(self):
        self.spec['python_version'] = '../../usr'
        with self.assertRaisesRegex(ContractError, 'Python'):
            desktop.recipe(self.sysroot, self.spec)
        self.spec['python_version'] = '3.14'
        self.spec['unreviewed'] = True
        with self.assertRaisesRegex(ContractError, 'schema'):
            desktop.recipe(self.sysroot, self.spec)

    def test_inventory_metadata_tampering_fails_verification(self):
        release = desktop.pack(self.sysroot, self.spec, self.root / 'desktop')
        (release / 'metadata/desktop-inventory.json').write_text('{}')
        with self.assertRaisesRegex(ContractError, 'digest'):
            artifact.verify(release)

    def test_cli_packages_the_desktop(self):
        path = self.root / 'input.json'
        path.write_text(json.dumps(self.spec))
        cli = Path(__file__).resolve().parents[1] / 'luminophore-runtime'
        result = subprocess.run([str(cli), 'pack-desktop', '--sysroot', str(self.sysroot),
                                 '--input', str(path), '--output', str(self.root / 'desktop')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(json.loads(result.stdout)['release']).is_dir())
