"""Real temporary-directory cutovers, without starting a session or Lua."""
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from luminophore_shell.desktop_defaults import write_defaults
from luminophore_shell.settings_bootstrap import prepare
from luminophore_shell.settings_store import FILES, StoreError
from luminophore_shell.settings_migration import inspect


class SettingsBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        write_defaults(self.root/'defaults')
        self.defaults=self.root/'defaults/desktop'
        self.target=self.root/'user'

    def test_new_install_has_five_valid_files_and_no_lua(self):
        self.assertEqual(prepare(self.target,self.defaults),self.target)
        self.assertEqual({p.name for p in self.target.iterdir()},set(FILES))
        self.assertEqual(tomllib.loads((self.target/'settings.toml').read_text())['schema_version'],1)
        before={p.name:p.read_bytes() for p in self.target.iterdir()}
        prepare(self.target,self.defaults)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.target.iterdir()})

    def test_legacy_values_comments_and_complete_original_tree_survive(self):
        self.target.mkdir()
        (self.target/'shell.toml').write_text('# user setting\n[theme]\nui_scale=1.1 # keep\n')
        (self.target/'variables.lua').write_text('TERMINAL = "foot"\nLEFT_MONITOR="DP-2"\nRIGHT_MONITOR="DP-1"\nPRIMARY_MONITOR=LEFT_MONITOR\n')
        (self.target/'asset.bin').write_bytes(b'\x00resource')
        original={p.name:p.read_bytes() for p in self.target.iterdir()}
        prepare(self.target,self.defaults)
        text=(self.target/'settings.toml').read_text()
        self.assertIn('# user setting',text); self.assertIn('# keep',text)
        self.assertEqual(tomllib.loads(text)['native']['applications']['terminal'],'foot')
        backup=next(self.root.glob('.luminophore-migration-*/source-tree'))
        self.assertEqual(original,{p.name:p.read_bytes() for p in backup.iterdir()})
        self.assertEqual((self.target/'asset.bin').read_bytes(),b'\x00resource')

    def test_unknown_lua_is_not_executed_or_activated(self):
        self.target.mkdir()
        lua=self.target/'custom.lua'; lua.write_text('os.execute("touch sentinel")\n')
        with self.assertRaisesRegex(StoreError,'requires resolution'): prepare(self.target,self.defaults)
        self.assertEqual(list(self.target.iterdir()),[lua])
        self.assertFalse((self.root/'sentinel').exists())

    def test_symlink_input_cannot_be_replaced(self):
        self.target.symlink_to(self.defaults,target_is_directory=True)
        with self.assertRaises(StoreError): prepare(self.target,self.defaults)
        self.assertTrue(self.target.is_symlink())

    def test_partial_native_bundle_is_preserved_and_rejected(self):
        self.target.mkdir(); path=self.target/'settings.toml'; path.write_text('schema_version=1\n')
        with self.assertRaisesRegex(StoreError,'incomplete'): prepare(self.target,self.defaults)
        self.assertEqual(list(self.target.iterdir()),[path])

    def test_source_changed_during_copy_is_not_activated(self):
        self.target.mkdir(); path=self.target/'shell.toml'; path.write_text('[theme]\nui_scale=1.0\n')
        real=inspect
        def inspect_changed(source,**kwargs):
            if list(self.root.glob('.luminophore-native-*')): path.write_text('[theme]\nui_scale=1.2\n')
            return real(source,**kwargs)
        with patch('luminophore_shell.settings_bootstrap.inspect',side_effect=inspect_changed):
            with self.assertRaisesRegex(StoreError,'changed during'): prepare(self.target,self.defaults)
        self.assertIn('1.2',path.read_text())
        self.assertFalse((self.target/'settings.toml').exists())

    def test_conflicting_duplicate_legacy_variables_are_rejected(self):
        (self.target/'config').mkdir(parents=True)
        (self.target/'variables.lua').write_text('TERMINAL="foot"\n')
        (self.target/'config/variables.lua').write_text('TERMINAL="ghostty"\n')
        with self.assertRaisesRegex(StoreError,'conflicting'): prepare(self.target,self.defaults)
        self.assertFalse((self.target/'settings.toml').exists())

    def test_already_inspected_partial_toml_is_preserved_in_backup_and_imported(self):
        self.target.mkdir()
        (self.target/'shell.toml').write_text('[theme]\nui_scale=1.1\n')
        monitors='schema_version=1\n[outputs."DP-1"]\nmode="1920x1080@144"\n'
        (self.target/'monitors.toml').write_text(monitors)
        prepare(self.target,self.defaults)
        self.assertEqual(tomllib.loads((self.target/'monitors.toml').read_text())['outputs']['DP-1']['mode'],'1920x1080@144')
        backup=next(self.root.glob('.luminophore-migration-*/source-tree'))
        self.assertEqual((backup/'monitors.toml').read_text(),monitors)
