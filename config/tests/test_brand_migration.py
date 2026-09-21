from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from luminophore_shell.brand_migration import Migration, apply, plan

class BrandMigrationTests(unittest.TestCase):
    def test_copy_preserves_original_and_bytes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); old = root/'old'; old.mkdir(); (old/'palette.json').write_text('{"color":"#abcdef"}')
            apply([Migration(old, root/'new')])
            self.assertEqual((old/'palette.json').read_bytes(), (root/'new/palette.json').read_bytes())
    def test_existing_destination_aborts_whole_plan(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); old=root/'old'; old.mkdir(); existing=root/'existing'; existing.mkdir()
            with self.assertRaises(FileExistsError):
                apply([Migration(old,root/'new'), Migration(old,existing)])
            self.assertFalse((root/'new').exists())
    def test_live_socket_blocks_copy(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); old=root/'old'; old.mkdir(); runtime=root/'runtime'; (runtime/'neon-shell').mkdir(parents=True); (runtime/'neon-shell/control.sock').touch()
            with self.assertRaises(RuntimeError): apply([Migration(old,root/'new')],runtime)
            self.assertFalse((root/'new').exists())
    def test_xdg_paths_and_no_runtime_copy(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'state/neon-shell').mkdir(parents=True)
            entries=plan(root,{'XDG_STATE_HOME':str(root/'state'),'XDG_RUNTIME_DIR':str(root/'run')})
            self.assertEqual(entries,[Migration(root/'state/neon-shell',root/'state/luminophore-shell')])

    def test_icon_label_changes_only_in_destination(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); old=root/'old'; old.mkdir()
            (old/'index.theme').write_text('[Icon Theme]\nName=Neon Shell Arcticons\n')
            new=root/'luminophore-shell-arcticons'
            apply([Migration(old,new)])
            self.assertIn('Name=Luminophore Shell Arcticons', (new/'index.theme').read_text())
            self.assertIn('Name=Neon Shell Arcticons', (old/'index.theme').read_text())

    def icon_fixture(self, root):
        import hashlib
        data = root / 'data'
        old = data / 'neon-shell'
        objects = old / 'icon-objects'
        objects.mkdir(parents=True)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        digest = hashlib.sha256(svg).hexdigest()
        (objects / f'{digest}.svg').write_bytes(svg)
        (old / 'generated-icons.toml').write_text(
            'version = 1\n[icons."example.desktop"]\n'
            f'target_name = "example"\nobject_sha256 = "{digest}"\n'
            f'source_sha256 = "{digest}"\nstrategy = "manual"\napproved_at = 123\n')
        overlay = data / 'icons/neon-shell-arcticons/scalable/apps'
        overlay.mkdir(parents=True)
        (overlay / 'example.svg').write_bytes(svg)
        (overlay.parent.parent / 'index.theme').write_text('[Icon Theme]\nName=Neon Shell Arcticons\n')
        return data, digest

    def test_approved_icons_and_objects_migrate_without_plugins(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data, digest = self.icon_fixture(root)
            (data / 'neon-shell/plugins').mkdir()
            apply(plan(root, {'XDG_DATA_HOME': str(data)}))
            self.assertEqual((data / 'neon-shell/generated-icons.toml').read_bytes(),
                             (data / 'luminophore-shell/generated-icons.toml').read_bytes())
            self.assertTrue((data / f'luminophore-shell/icon-objects/{digest}.svg').is_file())
            self.assertFalse((data / 'luminophore-shell/plugins').exists())

    def test_retry_after_rc5_overlay_copy_and_existing_state(self):
        import shutil
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data, digest = self.icon_fixture(root)
            shutil.copytree(data / 'icons/neon-shell-arcticons', data / 'icons/luminophore-shell-arcticons')
            index = data / 'icons/luminophore-shell-arcticons/index.theme'
            index.write_text(index.read_text().replace('Neon Shell', 'Luminophore Shell'))
            for brand in ('neon-shell', 'luminophore-shell'):
                state = root / '.local/state' / brand; state.mkdir(parents=True)
                (state / 'state.json').write_text(brand)
            entries = plan(root, {'XDG_DATA_HOME': str(data)})
            apply(entries)
            apply(entries)
            self.assertTrue((data / 'luminophore-shell/generated-icons.toml').exists())
            self.assertEqual((root / '.local/state/luminophore-shell/state.json').read_text(), 'luminophore-shell')

    def test_bad_approved_object_aborts_before_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data, digest = self.icon_fixture(root)
            (data / f'neon-shell/icon-objects/{digest}.svg').write_text('corrupt')
            with self.assertRaisesRegex(ValueError, 'hash'):
                apply(plan(root, {'XDG_DATA_HOME': str(data)}))
            self.assertFalse((data / 'icons/luminophore-shell-arcticons').exists())

    def test_conflicting_destination_approval_is_preserved(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data, _ = self.icon_fixture(root)
            target = data / 'luminophore-shell'; target.mkdir()
            (target / 'generated-icons.toml').write_text('version = 1\n')
            with self.assertRaises(FileExistsError):
                apply(plan(root, {'XDG_DATA_HOME': str(data)}))
            self.assertEqual((target / 'generated-icons.toml').read_text(), 'version = 1\n')
            self.assertFalse((data / 'icons/luminophore-shell-arcticons').exists())

    def test_bad_source_overlay_aborts_before_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data, _ = self.icon_fixture(root)
            (data / 'icons/neon-shell-arcticons/scalable/apps/example.svg').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'hash'):
                apply(plan(root, {'XDG_DATA_HOME': str(data)}))
            self.assertFalse((data / 'luminophore-shell').exists())

    def test_existing_new_overlay_conflict_aborts_before_approval_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data, _ = self.icon_fixture(root)
            target = data / 'icons/luminophore-shell-arcticons/scalable/apps'
            target.mkdir(parents=True)
            (target / 'example.svg').write_text('new-user-icon')
            with self.assertRaises(FileExistsError):
                apply(plan(root, {'XDG_DATA_HOME': str(data)}))
            self.assertFalse((data / 'luminophore-shell').exists())
            self.assertEqual((target / 'example.svg').read_text(), 'new-user-icon')

    def test_symlink_object_destination_is_not_followed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data, _ = self.icon_fixture(root)
            target = data / 'luminophore-shell'; target.mkdir()
            outside = root / 'outside'; outside.mkdir()
            (target / 'icon-objects').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(FileExistsError):
                apply(plan(root, {'XDG_DATA_HOME': str(data)}))
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((target / 'generated-icons.toml').exists())
