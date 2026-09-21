import json
import os
import tempfile
import unittest
from pathlib import Path
from luminophore_shell.keymap_snapshot import compile_keymap, import_keymaps, snapshot_text
from luminophore_shell.config import load_config_text, ConfigError
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_generation import edit_candidate, generation_config
from luminophore_shell.settings_store import SettingsPaths, FILES, StoreError

SOURCE='''xkb_keymap {
 xkb_keycodes { include "evdev+aliases(qwerty)" };
 xkb_types { include "complete" };
 xkb_compatibility { include "complete" };
 xkb_symbols { include "pc+us+inet(evdev)" };
};'''

class KeymapSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.paths=SettingsPaths(self.root/'config',self.root/'state',self.root/'cache')
        self.store=settings_store(self.paths)
        self.base=self.store._candidate({n:'schema_version = 1\n' for n in FILES})
        self.source=self.root/'keyboard#main.xkb';self.source.write_text(SOURCE)

    def test_import_survives_deleted_source_and_generation_reload(self):
        changed=edit_candidate(self.store,self.base,{'input.kb_file':str(self.source)})
        config=generation_config(self.store,changed)
        text=snapshot_text(config.input.kb_file,config.input.kb_snapshot)
        self.assertEqual(compile_keymap(text,includes=False),text)
        prepared=self.store.prepare(changed.documents,'');self.store.publish(prepared.id,'')
        self.source.unlink()
        self.assertEqual(self.store.current(),prepared)
        other=edit_candidate(self.store,prepared,{'input.repeat_delay':500})
        self.assertEqual(generation_config(self.store,other).input.kb_snapshot,config.input.kb_snapshot)
        cleared=edit_candidate(self.store,other,{'input.kb_file':''})
        self.assertEqual(generation_config(self.store,cleared).input.kb_snapshot,'')

    def test_same_path_reimport_and_device_override_reset(self):
        first=edit_candidate(self.store,self.base,{'input.kb_file':str(self.source)})
        old=generation_config(self.store,first).input.kb_snapshot
        self.source.write_text(SOURCE.replace('pc+us+','pc+de+'))
        second=edit_candidate(self.store,first,{'input.kb_file':str(self.source)})
        self.assertNotEqual(old,generation_config(self.store,second).input.kb_snapshot)
        device=edit_candidate(self.store,second,{'devices':{'keyboard':{'kb_file':''}}})
        self.assertEqual(generation_config(self.store,device).devices['keyboard'],{'kb_file':'','kb_snapshot':''})
        third=edit_candidate(self.store,device,{'devices':{'keyboard':{'kb_file':str(self.source)}}})
        self.assertIn('kb_snapshot',generation_config(self.store,third).devices['keyboard'])
        saved=generation_config(self.store,third).devices['keyboard']['kb_snapshot']
        self.source.unlink()
        edited=edit_candidate(self.store,third,{'devices':{'keyboard':{'kb_file':str(self.source),'repeat_delay':400}}})
        self.assertEqual(generation_config(self.store,edited).devices['keyboard']['kb_snapshot'],saved)

    def test_direct_toml_import_and_invalid_file_leave_store_unchanged(self):
        self.paths.config.mkdir()
        for name,text in self.base.documents.items():(self.paths.config/name).write_text(text)
        (self.paths.config/'settings.toml').write_text('schema_version=1\n[input]\nkb_file='+json.dumps(str(self.source)))
        candidate=self.store.read_candidate()
        self.assertTrue(generation_config(self.store,candidate).input.kb_snapshot)
        self.source.write_text('invalid keymap')
        with self.assertRaises(StoreError):self.store.read_candidate()
        self.assertIsNone(self.store.current())
        with self.assertRaises(ConfigError):load_config_text('[input]\nkb_file='+json.dumps(str(self.source)))

    def test_nonregular_oversized_and_missing_source(self):
        fifo=self.root/'fifo';os.mkfifo(fifo)
        for path in (fifo,self.root,self.root/'missing'):
            with self.subTest(path=path),self.assertRaises(StoreError):edit_candidate(self.store,self.base,{'input.kb_file':str(path)})
        self.source.write_bytes(b'x'*(1024*1024+1))
        with self.assertRaises(StoreError):edit_candidate(self.store,self.base,{'input.kb_file':str(self.source)})

    def test_snapshot_source_mismatch_and_inherited_mismatch_rejected(self):
        config=generation_config(self.store,edit_candidate(self.store,self.base,{'input.kb_file':str(self.source)}))
        with self.assertRaises(StoreError):snapshot_text('/other',config.input.kb_snapshot)
        text='[input]\nkb_file='+json.dumps(config.input.kb_file)+'\nkb_snapshot='+json.dumps(config.input.kb_snapshot)+'\n[devices.keyboard]\nkb_file="/other"'
        with self.assertRaises(ConfigError):load_config_text(text)
