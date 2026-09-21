from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest

from luminophore_shell.binding_registry import default_registry
from luminophore_shell.settings_bundle import decode_bundle, settings_store
from luminophore_shell.settings_store import FILES, SettingsPaths, StoreError


def documents():
    return {name: {'schema_version': 1} for name in FILES}


class SettingsBundleTests(unittest.TestCase):
    def decode(self, raw):
        return decode_bundle(raw, Path('/tmp/settings.toml'))

    def test_defaults_and_input_ownership(self):
        raw = documents()
        original = deepcopy(raw)
        result = self.decode(raw)
        self.assertEqual(result.settings.compositor.default_view_columns, 1)
        self.assertEqual(result.bindings, default_registry())
        self.assertEqual(raw, original)

    def test_existing_shell_fixture(self):
        raw = documents()
        raw['settings.toml'].update(tomllib.loads((Path(__file__).parents[1] / 'luminophore_shell/config.toml').read_text()))
        self.decode(raw)

    def test_unknown_tables_and_versions(self):
        for name in FILES:
            with self.subTest(name=name):
                raw = documents(); raw[name]['typo'] = {}
                with self.assertRaises(StoreError): self.decode(raw)
                raw = documents(); raw[name]['schema_version'] = True
                with self.assertRaises(StoreError): self.decode(raw)

    def test_visual_invalid_does_not_silently_recover(self):
        for visual in ({'intensity': 99.0}, {'preset': 'invalid'}, {'enabled': 1}, {'surprise': True}):
            raw = documents(); raw['settings.toml']['visual'] = visual
            with self.assertRaisesRegex(StoreError, 'settings.toml'): self.decode(raw)

    def test_shell_scalar_types_and_retired_keys_rejected(self):
        for section, key, value in [('layout', 'edge_margin', True), ('theme', 'ui_scale', float('nan')),
                                    ('theme', 'high_contrast', 1), ('theme', 'body_font', 2),
                                    ('layout', 'drop_halo', True)]:
            raw = documents(); raw['settings.toml'][section] = {key: value}
            with self.assertRaisesRegex(StoreError, 'settings.toml'): self.decode(raw)

    def test_monitor_positions_are_offline_and_strict(self):
        raw = documents(); raw['monitors.toml']['positions'] = {'DP-1': [-1920, 0]}
        self.assertEqual(self.decode(raw).monitors, {'DP-1': (-1920, 0)})
        for point in ([True, 0], [0.0, 0], [100001, 0], [0], '0,0'):
            raw['monitors.toml']['positions']['DP-1'] = point
            with self.assertRaisesRegex(StoreError, 'monitors.toml'): self.decode(raw)

    def test_placement_and_bundle_preserve_semantics(self):
        raw = documents()
        raw['placement.toml']['rules'] = {'org.example.Editor': 'right'}
        raw['bundles.toml']['bundles'] = [{'id': 'work', 'name': 'Work', 'chord': '', 'items': [
            {'desktop_id': 'org.example.Editor.desktop', 'new_instance': False}]}]
        result = self.decode(raw)
        self.assertEqual(result.placement['org.example.Editor'], 'right')
        self.assertFalse(result.bundles[0]['items'][0]['new_instance'])
        result.bundles[0]['items'][0]['new_instance'] = True
        self.assertFalse(raw['bundles.toml']['bundles'][0]['items'][0]['new_instance'])

    def test_binding_flags_types_and_unknown_action(self):
        key = default_registry().actions[0].action_id
        for name, value in [('missing', {}), (key, {'flags': {'locked': 1}}),
                            (key, {'disabled': True, 'chord': 'SUPER + K'}), (key, {'disabled': 'yes'})]:
            raw = documents(); raw['bindings.toml']['actions'] = {name: value}
            with self.assertRaisesRegex(StoreError, 'bindings.toml'): self.decode(raw)

    def test_atomic_binding_group_and_recovery(self):
        registry = default_registry()
        grouped = next(a for a in registry.actions if a.group_id and a.chord)
        raw = documents(); raw['bindings.toml']['actions'] = {grouped.action_id: {'disabled': True}}
        with self.assertRaisesRegex(StoreError, 'atomic binding'): self.decode(raw)
        raw['bindings.toml']['actions'] = {a.action_id: {'disabled': True} for a in registry.actions if a.recovery}
        with self.assertRaises(ValueError): self.decode(raw)

    def test_cross_file_collision_uses_candidate_bindings(self):
        action = next(a for a in default_registry().actions if a.chord and not a.group_id and not a.recovery)
        raw = documents()
        raw['bindings.toml']['actions'] = {action.action_id: {'chord': 'SUPER + CTRL + F24'}}
        raw['bundles.toml']['bundles'] = [{'id':'work','name':'Work','chord':'ctrl + super + F24',
            'items':[{'desktop_id':'editor.desktop','new_instance':False}]}]
        with self.assertRaisesRegex(StoreError, 'conflicts'): self.decode(raw)
        raw['bindings.toml']['actions'][action.action_id] = {'disabled': True}
        self.decode(raw)

    def test_store_rejects_before_writing_and_recovers_valid_bundle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SettingsPaths(root/'config', root/'state', root/'cache')
            store = settings_store(paths)
            texts = {name: 'schema_version = 1\n' for name in FILES}
            texts['placement.toml'] += '[rules]\neditor = "diagonal"\n'
            with self.assertRaisesRegex(StoreError, 'placement.toml'): store.prepare(texts, '')
            self.assertFalse(paths.state.exists())
            texts['placement.toml'] = 'schema_version = 1\n[rules]\neditor = "right"\n'
            candidate = store.prepare(texts, '')
            self.assertIsNone(store.current())
            store.publish(candidate.id, '')
            self.assertEqual(store.recover().documents, texts)

    def test_collections_are_rejected_before_coercion(self):
        for section, key in [('taskbar', 'pinned'), ('launcher', 'fixed_apps'),
                             ('notifications', 'dnd_allowlist')]:
            for value in ('abc', 123, {}, [1], ['app', False], [''], [' '], ['App', 'app']):
                with self.subTest(section=section, value=value):
                    raw = documents(); raw['settings.toml'][section] = {key: value}
                    with self.assertRaisesRegex(StoreError, section + '.' + key):
                        self.decode(raw)

    def test_collection_tables_reject_wrong_shapes(self):
        for section, key, values in [
            ('launcher', 'preferred_actions', ['abc', [], {'app': 1}, {'': 'open'}]),
            ('theme', 'app_icon_aliases', ['abc', [], {'app': False}, {'app': ''}]),
            ('theme', 'fixed_monitor_palettes', ['abc', [], {'DP-1': '#ffffff'},
                                                {'DP-1': ['#ffffff', 1]}, {'DP-1': ['#ffffff']}])]:
            for value in values:
                with self.subTest(key=key, value=value):
                    raw = documents(); raw['settings.toml'][section] = {key: value}
                    with self.assertRaisesRegex(StoreError, section + '.' + key):
                        self.decode(raw)

    def test_valid_collection_values_and_comments_survive_store(self):
        text = ('schema_version = 1\n# preserve this comment\n'
                '[taskbar]\npinned = ["org.example.Editor"]\n'
                '[launcher]\nfixed_apps = ["org.example.Editor"]\n'
                '[launcher.preferred_actions]\n"org.example.Editor" = "new-window"\n'
                '[notifications]\ndnd_allowlist = ["org.example.Chat"]\n'
                '[theme.app_icon_aliases]\n"org.example.Editor" = "text-editor"\n'
                '[theme.fixed_monitor_palettes]\nDP-1 = ["#ffffff", "#123456"]\n')
        with TemporaryDirectory() as directory:
            root = Path(directory); paths = SettingsPaths(root/'config', root/'state', root/'cache')
            store = settings_store(paths)
            texts = {name: 'schema_version = 1\n' for name in FILES}
            texts['settings.toml'] = 'schema_version = 1\n[taskbar]\npinned = "abc"\n'
            with self.assertRaisesRegex(StoreError, 'taskbar.pinned'):
                store.prepare(texts, '')
            self.assertEqual(list(root.iterdir()), [])
            texts['settings.toml'] = text
            candidate = store.prepare(texts, '')
            store.publish(candidate.id, '')
            self.assertEqual(store.recover().documents['settings.toml'], text)
            raw = documents(); raw['settings.toml'] = tomllib.loads(text)
            parsed = self.decode(raw).settings
            self.assertEqual(parsed.taskbar.pinned, ('org.example.Editor',))
            self.assertEqual(parsed.launcher.preferred_actions, (('org.example.Editor', 'new-window'),))
            self.assertEqual(parsed.theme.fixed_monitor_palettes, (('DP-1', '#ffffff', '#123456'),))

if __name__ == '__main__': unittest.main()
