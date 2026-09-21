import tempfile
import tomllib
import unittest
from pathlib import Path
from luminophore_shell.input_settings import decode_devices, validate_input
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_generation import edit_candidate, generation_config
from luminophore_shell.settings_store import SettingsPaths, FILES, StoreError

class DeviceSettingsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.store=settings_store(SettingsPaths(root/'config',root/'state',root/'cache'))
        self.base=self.store._candidate({name:'schema_version = 1\n' for name in FILES})

    def test_partial_override_preserves_global_and_missing_device(self):
        rules={'unplugged-keyboard':{'kb_layout':'us','repeat_rate':45}}
        candidate=edit_candidate(self.store,self.base,{'devices':rules,'input.kb_layout':'de'})
        config=generation_config(self.store,candidate)
        self.assertEqual(config.input.kb_layout,'de')
        self.assertEqual(config.devices,rules)
        changed=edit_candidate(self.store,candidate,{'devices':{'unplugged-mouse':{'sensitivity':0.42}}})
        self.assertEqual(generation_config(self.store,changed).devices,{'unplugged-mouse':{'sensitivity':0.42}})
        removed=edit_candidate(self.store,changed,{'devices':{}})
        self.assertEqual(generation_config(self.store,removed).devices,{})
        self.assertEqual(generation_config(self.store,removed).input.kb_layout,'de')
        for name in FILES:
            if name!='settings.toml':self.assertEqual(removed.documents[name],self.base.documents[name])

    def test_invalid_types_names_fields_bounds_and_text_are_rejected(self):
        for raw in ([],{'Bad Name':{}},{'a':[]},{'a':{'enabled':False}}, {'a':{'force_no_accel':False}}, {'a':{'repeat_rate':True}},
                    {'a':{'sensitivity':1}}, {'a':{'sensitivity':float('nan')}}, {'a':{'repeat_rate':201}},
                    {'a':{'kb_layout':'a\n'}},{'a':{'kb_layout':'a'*513}}, {str(i):{} for i in range(33)}):
            with self.subTest(raw=raw),self.assertRaises(StoreError):decode_devices(raw)
        with self.assertRaises(StoreError):validate_input({1:'us'})

    def test_quoted_table_and_unrelated_comments_survive(self):
        docs=dict(self.base.documents)
        docs['settings.toml']+='["devices"."a"]\nkb_layout="us"\n[input]\nkb_layout="de" # preserve\n'
        base=self.store._candidate(docs)
        changed=edit_candidate(self.store,base,{'devices':{'b':{'kb_variant':''}}})
        self.assertEqual(tomllib.loads(changed.documents['settings.toml'])['devices'],{'b':{'kb_variant':''}})
        self.assertIn('# preserve',changed.documents['settings.toml'])

    def test_inline_device_domain_cannot_be_silently_retained(self):
        docs=dict(self.base.documents);docs['settings.toml']+='devices = { a = {kb_layout="us"} }\n'
        base=self.store._candidate(docs)
        candidate=edit_candidate(self.store,base,{'devices':{}})
        self.assertEqual(generation_config(self.store,candidate).devices,{})
        self.assertFalse(self.store.root.exists())

class NativeDeviceDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import subprocess
        temp=tempfile.TemporaryDirectory();cls.addClassCleanup(temp.cleanup)
        directory=Path(temp.name)
        native=Path(__file__).resolve().parents[2]/'compositor/src/config/luminophore'
        source=directory/'decoder.cpp'
        source.write_text('''#include "SettingsGeneration.hpp"
#include <iostream>
using namespace Luminophore::Settings;
int main(int argc,char**argv) {
 if(argc!=3)return 2;
 try {auto generation=CSettingsGenerations(argv[1]).load(argv[2]);
 std::cout<<generation.devices.size();return 0;}
 catch(const std::exception& error){std::cerr<<error.what();return 1;}
}''')
        cls.binary=directory/'decoder'
        subprocess.run(['g++','-std=c++23','-I',str(native),str(source),
                        *[str(native/(name+'.cpp')) for name in ('GeneratedSettings','SettingsGeneration','MonitorSettings','DesktopSettings')],
                        '-ltomlplusplus','-lcrypto','-o',str(cls.binary)],capture_output=True,text=True,check=True)

    def test_native_and_shell_agree_on_device_documents(self):
        import subprocess
        from luminophore_shell.settings_store import SettingsStore
        for text,valid in (('[input]\nkb_file="/missing"',False),
                           ('[devices.keyboard]\nkb_file="/missing"',False),
                           ('[input]\nkb_snapshot="orphan"',False),
                           ('[input]\naccel_profile="custom 0.5 0 1"\nscroll_points="1 0 2"\n[devices.mouse]\nscroll_points="2 0 3"',True),
                           ('[devices.mouse]\naccel_profile="flat"',True),
                           ('[input]\naccel_profile="custom 1 0 2junk"',False),
                           ('[input]\naccel_profile="custom 0 0 1"',False),
                           ('[input]\naccel_profile="custom 1 -1 2"',False),
                           ('[input]\naccel_profile="custom 1 0 inf"',False),
                           ('[devices.mouse]\naccel_profile="custom 1 0 1e999"',False),
                           ('[devices.mouse]\nscroll_points="1 0 1"',False),
                           ('[devices.keyboard]\nkb_layout="de"\nrepeat_rate=40\n',True),
                           ('[devices.mouse]\nsensitivity=0.42\n',True),
                           ('[devices.touchpad]\ntap_to_click=false\ntap_and_drag=true\ndrag_lock=2\ntap_button_map="lmr"\n',True),
                           ('[devices.pen]\nregion_position_x=0.3\nactive_area_size_y=120.5\noutput="DP-9"\n',True),
                           ('[devices.pen]\nregion_position_x=1\n',False),
                           ('[devices.pen]\noutput="../DP-1"\n',False),
                           ('[devices.pen]\nenabled=false\n',False),
                           ('[tablettool]\npressure_range_max=0.4\n[devices.pen]\npressure_range_min=0.8\n',False),
                           ('[tablettool]\npressure_range_max=-1.0\n[devices.pen]\npressure_range_min=0.8\n',True),
                           ('[devices.mouse]\nscroll_method="on_button_down"\nscroll_button=274\nscroll_button_lock=true\nrotation=90\n',True),
                           ('[devices.mouse]\nscroll_method="other"\n',False),
                           ('[devices.mouse]\nrotation=360\n',False),
                           ('[devices.keyboard]\nnumlock_by_default=true\nresolve_binds_by_sym=false\n',True),
                           ('[devices.keyboard]\nnumlock_by_default=1\n',False),
                           ('[devices.keyboard]\nresolve_binds_by_sym="true"\n',False),
                           ('[devices.mouse]\nfollow_mouse=1\n',False),
                           ('[devices.mouse]\nfollow_mouse_threshold=0.0\n',False),
                           ('[devices.mouse]\nmouse_refocus=true\n',False),
                           ('[devices.mouse]\nfollow_mouse_shrink=0\n',False),
                           ('[devices.mouse]\noff_window_axis_events=1\n',False),
                           ('[devices.mouse]\nemulate_discrete_scroll=1\n',False),
                           ('[devices.mouse]\nfocus_on_close=1\n',False),
                           ('[devices.mouse]\nfloat_switch_override_focus=1\n',False),
                           ('[devices.touchpad]\ntap_to_click=1\n',False),
                           ('[devices.touchpad]\ndrag_lock=3\n',False),
                           ('[devices.touchpad]\ntap_button_map="other"\n',False),
                           ('[devices.mouse]\nforce_no_accel=true\n',False),
                           ('[devices.mouse]\nsensitivity=1\n',False),
                           ('[devices.keyboard]\nrepeat_rate=true\n',False),
                           ('[devices.keyboard]\nkb_layout="bad/path"\n',False),
                           ('[devices.keyboard]\nunknown=true\n',False),
                           ('[devices."Upper Case"]\nkb_layout="de"\n',False)):
            with self.subTest(text=text),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);paths=SettingsPaths(root/'config',root/'state',root/'cache')
                store=SettingsStore(paths,lambda _:None)
                docs={name:'schema_version = 1\n' for name in FILES};docs['settings.toml']+=text
                generation=store.prepare(docs,'')
                result=subprocess.run([str(self.binary),str(store.root),generation.id],capture_output=True,text=True)
                self.assertEqual(result.returncode==0,valid,result.stderr)
                if valid:self.assertEqual(result.stdout,'1')
                if valid:settings_store(paths)._candidate(docs)
                else:
                    with self.assertRaises(StoreError):settings_store(paths)._candidate(docs)
