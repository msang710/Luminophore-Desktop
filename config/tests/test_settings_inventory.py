import copy
import json
from pathlib import Path
import unittest

from luminophore_shell.settings_inventory import discover, validate


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/schema/legacy-inventory.json"


class SettingsInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())

    def test_current_sources_have_explicit_ownership(self):
        self.assertEqual(validate(ROOT, self.manifest), [])

    def test_missing_mapping_is_not_silently_accepted(self):
        manifest = copy.deepcopy(self.manifest)
        removed = manifest['entries'].pop(0)
        self.assertIn('unmapped declaration: ' + removed['key'], validate(ROOT, manifest))

    def test_changed_default_requires_review(self):
        manifest = copy.deepcopy(self.manifest)
        row = next(r for r in manifest['entries'] if r['key'].startswith('shell:'))
        row['fingerprint'] = '0' * 64
        self.assertIn('changed declaration: ' + row['key'], validate(ROOT, manifest))

    def test_duplicate_and_empty_destinations_fail(self):
        manifest = copy.deepcopy(self.manifest)
        row = manifest['entries'][0]
        row['destination'] = ''
        manifest['entries'].append(copy.deepcopy(row))
        errors = validate(ROOT, manifest)
        self.assertIn('duplicate mapping: ' + row['key'], errors)
        self.assertIn('missing destination/check: ' + row['key'], errors)

    def test_runtime_commands_included(self):
        entries = discover(ROOT)
        for name in ('live_pip', 'shell_projection', 'spatial_drag_begin'):
            self.assertTrue(any(k.startswith('command:') and k.endswith(':' + name) for k in entries), name)

    def test_package_session_and_greeter_entrypoints_are_in_scope(self):
        entries = discover(ROOT)
        for path in ('Luminophore-OS/runtime/luminophore_runtime/desktop_session.py',
                     'Luminophore-OS/runtime/luminophore_runtime/desktop.py',
                     'Luminophore-OS/runtime/defaults/hyprland.lua'):
            self.assertIn('source:' + path, entries)

    def test_assigned_ownership_is_not_connected_runtime(self):
        self.assertTrue(all(r['migration_state'] != 'connected' for r in self.manifest['entries']))
        manifest = copy.deepcopy(self.manifest)
        row = next(r for r in manifest['entries'] if r['key'] == 'shell:LayoutConfig.panel_height')
        row['migration_state'] = 'connected'
        manifest['routes'][row['route']]['legacy_writer_active'] = True
        errors = validate(ROOT, manifest)
        for message in ('unconnected consumer', 'missing production call sites',
                        'legacy writer still active', 'missing per-setting integration coverage'):
            self.assertIn(message + ': ' + row['key'], errors)

    def test_missing_consumer_symbol_fails_even_for_pending_route(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['routes']['shell-current']['evidence']['consumer'] = {
            'path': 'config/luminophore_shell/app.py', 'symbol': 'nonexistentSettingsConsumer()'}
        self.assertTrue(any(e.startswith('invalid consumer reference:') for e in validate(ROOT, manifest)))

    def test_test_only_call_site_cannot_prove_product_connection(self):
        manifest = copy.deepcopy(self.manifest)
        row = manifest['entries'][0]
        row['migration_state'] = 'connected'
        manifest['routes'][row['route']]['calls'] = [{
            'path': 'config/tests/test_settings_inventory.py', 'symbol': 'validate(ROOT, manifest)'}]
        self.assertIn('invalid production call site: ' + row['key'], validate(ROOT, manifest))

    def test_every_row_requires_an_explicit_route(self):
        manifest = copy.deepcopy(self.manifest)
        row = manifest['entries'][0]
        del row['route']
        self.assertIn('missing migration route: ' + row['key'], validate(ROOT, manifest))


if __name__ == '__main__':
    unittest.main()
