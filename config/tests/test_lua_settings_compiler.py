from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from luminophore_shell.config import load_config_text

class LuaCompilerTests(unittest.TestCase):
    def test_all_scalar_domains_are_mapped(self):
        from luminophore_shell.lua_settings import OPTION_MAP, owned_values
        c = load_config_text('[compositor]\nshadow_offset_x=1.5\n', Path('/tmp/config/shell.toml'))
        values = owned_values(c)
        missing = set(values) - set(OPTION_MAP)
        self.assertEqual(missing, {k for k in values if k.startswith(('motion.', 'visual.'))})

    def test_full_toml_parser_and_complex_values(self):
        from luminophore_shell.lua_settings import render
        c = load_config_text('''[input]
kb_options = "korean:ralt_hangul" # preserved
[compositor]
shadow_offset_x = 1.5
shadow_offset_y = -2.0
shadow_color = "ff112233 ff445566 90deg"
float_gap_top = 3
[devices.test-mouse]
sensitivity = 0.25
''', Path('/tmp/config/shell.toml'))
        lua=render(c)
        self.assertIn('["input.kb_options"] = "korean:ralt_hangul"', lua)
        self.assertIn('["decoration.shadow.offset"] = {1.5, -2.0}', lua)
        self.assertIn('rgba(112233ff)', lua)
        self.assertIn('["top"] = 3', lua)
        self.assertIn('hl.device(', lua)
        self.assertIn('test-mouse', lua)

    def test_atomic_compilation_output(self):
        from luminophore_shell.lua_settings import compile_config
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'shell.toml'; p.write_text('[compositor]\nborder_size=0\n')
            target=compile_config(p)
            self.assertEqual(target,p.parent/'config/luminophore_owned.lua')
            self.assertIn('["general.border_size"] = 0', target.read_text())

    def test_migration_preserves_explicit_choices_and_comments(self):
        from luminophore_shell.lua_settings import migrate_legacy_defaults
        import tomllib
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'shell.toml'; p.write_text('[input]\nkb_options="custom" # retained\n')
            self.assertTrue(migrate_legacy_defaults(p))
            self.assertIn('# retained',p.read_text())
            self.assertEqual(tomllib.loads(p.read_text())['input']['kb_options'],'custom')
            self.assertFalse(migrate_legacy_defaults(p))

    def test_device_write_supports_structured_replacement(self):
        from luminophore_shell.config import render_config_patch
        import tomllib
        original='[compositor]\nborder_size=0 # keep\n[devices.old]\nsensitivity=0.1\n'
        result=render_config_patch(original,{'devices':{'new':{'sensitivity':0.4}}})
        self.assertEqual(tomllib.loads(result)['devices'],{'new':{'sensitivity':0.4}})
        self.assertIn('# keep',result)

    def test_native_mapping_does_not_drift(self):
        import re
        from luminophore_shell.lua_settings import OPTION_MAP
        source=(Path(__file__).resolve().parents[2]/'compositor/src/config/luminophore/NativeSettingsConsumers.cpp').read_text()
        native=dict(re.findall(r'\{"((?:compositor|input|touchpad|tablet|tablettool|touchdevice|virtualkeyboard)\.[^"]+)", (?:slot<[^>]+>|\w+Slot)\("([^"]+)"',source))
        for key,value in native.items():
            self.assertEqual(OPTION_MAP[key],value,key)

    def test_compiled_module_executes_and_uses_lua_aliases(self):
        from luminophore_shell.lua_settings import render
        import subprocess
        config=load_config_text('[tablet]\nregion_position_y=2.0\n[devices.test]\nregion_position_x=1.0\ntap_to_click=true\n',Path('/tmp/shell.toml'))
        script='''local calls=0
hl={config=function(values) assert(values["input.touchpad.tap_to_click"]~=nil); calls=calls+1 end,
device=function(values) assert(values.region_position[2]==2.0); assert(values.tap_to_click==true) end,
animation=function(values) assert(type(values.speed)=="number") end}
local module=(function()
'''+render(config)+'''end)()
assert(calls==0)
module.apply()
assert(calls==1)
'''
        result=subprocess.run(['lua','-'],input=script,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
