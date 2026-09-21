"""Migration must preserve data, reject executable input, and never activate it."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import tomllib
import unittest
from unittest.mock import patch

from luminophore_shell import settings_migration as migration
from luminophore_shell.settings_store import FILES, StoreError


class SettingsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'old'
        self.source.mkdir()
        self.output = self.root / 'candidate'

    def write(self, name, value):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def test_toml_comments_and_values_survive_without_publishing(self):
        self.write('shell.toml', '# personal\n[compositor]\nrounding = 19\n')
        result = migration.inspect(self.source)
        self.assertFalse(result.unsupported)
        self.assertIn('# personal', result.documents['settings.toml'])
        self.assertEqual(tomllib.loads(result.documents['settings.toml'])['compositor']['rounding'], 19)
        migration.stage(result, self.source, self.output)
        self.assertEqual(set(FILES), {p.name for p in self.output.glob('*.toml')})
        self.assertEqual((self.source/'shell.toml').read_text(), '# personal\n[compositor]\nrounding = 19\n')
        self.assertFalse((self.root/'completed.json').exists())
        self.assertEqual(migration.stage(result, self.source, self.output), self.output)

    def test_static_input_and_monitor_values_are_imported(self):
        self.write('inputs.lua', 'hl.config({input={kb_options="korean:ralt_hangul", sensitivity=-0.25}})')
        self.write('monitors.lua', 'hl.monitor({output="DP-2",mode="1920x1080@144",position="-1920x0",scale=1})')
        result = migration.inspect(self.source)
        self.assertFalse(result.unsupported)
        self.assertEqual(tomllib.loads(result.documents['settings.toml'])['input']['sensitivity'], -.25)
        self.assertEqual(tomllib.loads(result.documents['monitors.toml'])['outputs']['DP-2']['position'], [-1920, 0])

    def test_executable_or_unrepresented_lua_is_reported_without_writes(self):
        for text in ('os.execute("touch /tmp/no")', 'hl.config({input={sensitivity=calculate()}})',
                     'hl.gesture({fingers=3, direction="down", action="close"})',
                     'hl.config({unknown={value=true}})'):
            with self.subTest(text=text):
                self.write('inputs.lua', text)
                result = migration.inspect(self.source)
                self.assertTrue(result.unsupported)
                with self.assertRaises(StoreError): migration.stage(result, self.source, self.output)
                self.assertFalse(self.output.exists())

    def test_duplicate_sources_cannot_silently_override_values(self):
        self.write('inputs.lua', 'hl.config({input={sensitivity=0.5}})')
        self.write('config/inputs.lua', 'hl.config({input={sensitivity=0.7}})')
        result = migration.inspect(self.source)
        self.assertTrue(any('conflict' in error for error in result.unsupported))

    def test_generated_domains_validate_body_not_only_header(self):
        from luminophore_shell.placement_rules import encode
        from luminophore_shell.app_bundles import encode as bundles
        self.write('config/luminophore_placements.lua', encode({'org.example.App': 'right'}).decode())
        self.write('config/luminophore_bundles.lua', bundles([]).decode())
        result = migration.inspect(self.source)
        self.assertFalse(result.unsupported)
        self.assertEqual(tomllib.loads(result.documents['placement.toml'])['rules'], {'org.example.App': 'right'})
        self.write('config/luminophore_placements.lua', encode({'org.example.App': 'right'}).decode()+'os.exit()')
        self.assertTrue(migration.inspect(self.source).unsupported)

    def test_staging_refuses_source_edits_or_existing_different_candidate(self):
        self.write('shell.toml', '[compositor]\nrounding=9\n')
        result = migration.inspect(self.source)
        self.write('shell.toml', '[compositor]\nrounding=10\n')
        with self.assertRaisesRegex(StoreError, 'changed'): migration.stage(result, self.source, self.output)
        self.assertFalse(self.output.exists())
        result = migration.inspect(self.source)
        migration.stage(result, self.source, self.output)
        (self.output/'settings.toml').write_text('schema_version=1\n')
        with self.assertRaises(StoreError): migration.stage(result, self.source, self.output)

    def test_symlink_and_new_source_file_are_not_ignored(self):
        self.write('shell.toml', '')
        result = migration.inspect(self.source)
        self.write('user.lua', 'return {}')
        with self.assertRaisesRegex(StoreError, 'changed'): migration.stage(result, self.source, self.output)
        (self.source/'user.lua').unlink()
        (self.source/'user.lua').symlink_to(self.source/'shell.toml')
        self.assertTrue(migration.inspect(self.source).unsupported)

    def test_exact_trusted_implementation_is_backed_up_not_executed(self):
        self.write('hyprland.lua', 'require("config.inputs")')
        digest = hashlib.sha256((self.source/'hyprland.lua').read_bytes()).hexdigest()
        result = migration.inspect(self.source, trusted={'hyprland.lua': {digest}})
        self.assertFalse(result.unsupported)
        migration.stage(result, self.source, self.output)
        self.assertEqual((self.output/'originals/hyprland.lua').read_bytes(), (self.source/'hyprland.lua').read_bytes())
        report = json.loads((self.output/'migration.json').read_text())
        self.assertEqual(report['sources']['hyprland.lua'], digest)
        self.assertFalse(report['activated'])

    def test_literal_strings_containing_comment_or_code_text_are_data(self):
        self.write('inputs.lua', 'hl.config({input={kb_options="--not a comment"}}) -- end\n')
        result = migration.inspect(self.source)
        self.assertFalse(result.unsupported)
        self.assertEqual(tomllib.loads(result.documents['settings.toml'])['input']['kb_options'], '--not a comment')

    def test_personal_output_vrr_and_mode_survive(self):
        self.write('monitors.lua', 'hl.monitor({output="DP-1", mode="1920x1080@144", position="1920x0", scale=1, vrr=2})')
        result = migration.inspect(self.source)
        self.assertFalse(result.unsupported)
        self.assertEqual(tomllib.loads(result.documents['monitors.toml'])['outputs']['DP-1']['vrr'], 2)

    def test_vrr_rejects_boolean_float_and_out_of_range(self):
        from luminophore_shell.monitor_settings import decode_monitors
        for value in (True, 1.0, -1, 4, '2'):
            with self.subTest(value=value), self.assertRaises(StoreError):
                decode_monitors({'outputs': {'DP-1': {'vrr': value}}})

    def test_shell_comments_survive_imported_input_values(self):
        self.write('shell.toml', '# personal\n[compositor]\nrounding = 19 # retain\n')
        self.write('inputs.lua', 'hl.config({input={sensitivity=0.5}})')
        result = migration.inspect(self.source)
        self.assertFalse(result.unsupported)
        self.assertIn('rounding = 19 # retain', result.documents['settings.toml'])

    def test_concurrent_destination_creation_is_not_overwritten(self):
        self.write('shell.toml', '')
        result = migration.inspect(self.source)
        read = migration._read_sources
        count = 0
        def source_read(root):
            nonlocal count
            count += 1
            if count == 2: self.output.mkdir()
            return read(root)
        with patch.object(migration, '_read_sources', source_read):
            with self.assertRaises(FileExistsError): migration.stage(result, self.source, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_backup_bytes_must_match_inspected_source_hashes(self):
        self.write('shell.toml', '# original\n')
        result = migration.inspect(self.source)
        result.originals['shell.toml'] = b'changed'
        with self.assertRaises(StoreError): migration.stage(result, self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_nested_config_is_not_silently_discarded(self):
        self.write('config/custom/input.lua', 'os.exit()')
        result = migration.inspect(self.source)
        self.assertTrue(any('config/custom/input.lua' in item for item in result.unsupported))

    def test_generated_binding_lua_without_json_preserves_custom_chord(self):
        from luminophore_shell.binding_registry import default_registry
        from luminophore_shell.generated_bindings import serialize_binding_lua
        registry = default_registry()
        action = next(a for a in registry.actions if not a.recovery and not a.group_id and a.chord)
        registry = registry.update({action.action_id: ('SUPER + F11', action.flags)})
        self.write('config/luminophore_bindings.lua', serialize_binding_lua(registry).decode())
        result = migration.inspect(self.source)
        self.assertFalse(result.unsupported)
        self.assertEqual(tomllib.loads(result.documents['bindings.toml'])['actions'][action.action_id]['chord'], 'SUPER + F11')

    def test_unsupported_data_is_reported_instead_of_crashing_inspection(self):
        for name, text in [('bindings.json', '{}'), ('shell.toml', '[input]\nsensitivity=inf\n'),
                           ('shell.toml', '[input]\nsensitivity=2026-09-21\n')]:
            with self.subTest(text=text):
                for path in self.source.iterdir(): path.unlink()
                self.write(name, text)
                result = migration.inspect(self.source)
                self.assertTrue(result.unsupported)
                with self.assertRaises(StoreError): migration.stage(result, self.source, self.output)
                self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
