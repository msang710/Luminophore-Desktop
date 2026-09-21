"""Common declarations control editable scalar keys; original TOML survives."""
import tempfile
import unittest
from pathlib import Path
import tomllib
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_store import SettingsPaths, FILES, StoreError
from luminophore_shell.settings_generation import edit_candidate, CANDIDATE_SPECS
from luminophore_shell.generated_settings import SCHEMA

class SettingsCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.store=settings_store(SettingsPaths(root/'config',root/'state',root/'cache'))
        self.base=self.store._candidate({name:'schema_version = 1\n' for name in FILES})

    def test_declared_editable_types_roundtrip(self):
        for field in SCHEMA['fields']:
            if field['visibility']!='setting':continue
            with self.subTest(key=field['key']):
                candidate=edit_candidate(self.store,self.base,{field['key']:field['default']})
                section,key=field['key'].split('.')
                value=tomllib.loads(candidate.documents['settings.toml'])[section][key]
                self.assertEqual(value,field['default']);self.assertIs(type(value),type(field['default']))
        self.assertTrue({f["key"] for f in SCHEMA["fields"] if f["visibility"] == "setting"} <= set(CANDIDATE_SPECS))

    def test_internal_and_compatibility_values_are_not_user_writes(self):
        for field in SCHEMA['fields']:
            if field['visibility']=='setting':continue
            with self.assertRaises(StoreError):edit_candidate(self.store,self.base,{field['key']:field['default']})

    def test_bool_integer_float_do_not_coerce(self):
        for changes in ({'motion.enabled':1},{'motion.speed':1},{'compositor.rounding':True},{'visual.intensity':float('nan')}):
            with self.assertRaises(StoreError):edit_candidate(self.store,self.base,changes)
        self.assertFalse(self.store.root.exists())

    def test_string_replacement_preserves_comments_and_other_files(self):
        docs=dict(self.base.documents);docs['settings.toml']+='\n[motion]\npreset = "balanced" # keep\n'
        candidate=edit_candidate(self.store,self.store._candidate(docs),{'motion.preset':'balanced','motion.enabled':False})
        self.assertIn('preset = "balanced" # keep',candidate.documents['settings.toml'])
        self.assertIs(tomllib.loads(candidate.documents['settings.toml'])['motion']['enabled'],False)
        for name in FILES:
            if name!='settings.toml':self.assertEqual(candidate.documents[name],docs[name])

    def test_joint_candidate_includes_shell_and_desktop_domains(self):
        changes = {'notifications.toast_limit': 4, 'taskbar.pinned': ['steam.desktop'],
                   'bindings.actions': {'window.close': {'chord': 'SUPER + F12'}},
                   'placement.rules': {'steam': 'left'},
                   'bundles.bundles': [],
                   'native': {'startup': ['steam -silent']}}
        candidate = edit_candidate(self.store, self.base, changes)
        raw = {name: tomllib.loads(text) for name, text in candidate.documents.items()}
        self.assertEqual(raw['settings.toml']['notifications']['toast_limit'], 4)
        self.assertEqual(raw['settings.toml']['taskbar']['pinned'], ['steam.desktop'])
        self.assertEqual(raw['bindings.toml']['actions']['window.close']['chord'], 'SUPER + F12')
        self.assertEqual(raw['placement.toml']['rules'], {'steam': 'left'})
        self.assertEqual(raw['settings.toml']['native']['startup'], ['steam -silent'])

    def test_inline_devices_can_be_changed_without_losing_other_domains(self):
        docs=dict(self.base.documents)
        docs['settings.toml'] += 'devices = { "pointer" = { sensitivity = 0.1 } }\n# preserved\n[native]\nstartup = ["steam"]\n'
        candidate=edit_candidate(self.store,self.store._candidate(docs),{'devices':{'pointer':{'sensitivity':0.2}}})
        parsed=tomllib.loads(candidate.documents['settings.toml'])
        self.assertEqual(parsed['devices']['pointer']['sensitivity'],0.2)
        self.assertEqual(parsed['native']['startup'],['steam'])
        self.assertIn('# preserved',candidate.documents['settings.toml'])
