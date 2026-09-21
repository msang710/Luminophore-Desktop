from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from luminophore_shell.config import InputConfig, TouchpadConfig, ConfigError, load_config_text
from luminophore_shell.owned_settings import DEFAULTS
from luminophore_shell.generated_settings import SCHEMA
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_generation import edit_candidate, generation_config
from luminophore_shell.settings_schema import settings_values, specs_for_category
from luminophore_shell.settings_store import SettingsPaths, FILES


class InputSettingsTests(unittest.TestCase):
    def test_defaults_and_visible_settings_share_contract(self):
        expected={k.removeprefix('input.'):v for k,v in DEFAULTS.items() if k.startswith('input.')}
        self.assertEqual(asdict(InputConfig()),expected)
        self.assertEqual({s.path for s in specs_for_category('input')},{f['key'] for f in SCHEMA['fields'] if f['visibility']=='setting' and f['key'].startswith(('input.','touchpad.','touchdevice.','virtualkeyboard.','tablet.','tablettool.'))})

    def test_bad_types_and_ranges_reject_without_coercion(self):
        for text in ('repeat_rate=true','repeat_rate=201','repeat_delay=-1','sensitivity=1',
                     'sensitivity=nan','sensitivity=1.1','scroll_factor=3.0','natural_scroll=1','unknown=true'):
            with self.subTest(text=text),self.assertRaises(ConfigError):
                load_config_text('[input]\n'+text)

    def test_candidate_readback_and_prior_generation_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            docs={name:'schema_version = 1\n' for name in FILES}
            docs['settings.toml']+='[input]\nrepeat_rate=25 # preserve\n'
            base=store._candidate(docs)
            changes={'input.repeat_rate':0,'input.repeat_delay':100,'input.sensitivity':-.5,
                     'input.scroll_factor':0.0,'input.natural_scroll':True,'input.left_handed':True,'input.force_no_accel':True}
            candidate=edit_candidate(store,base,changes)
            actual=settings_values(generation_config(store,candidate))
            for key,value in changes.items():self.assertEqual(actual[key],value)
            self.assertIn('repeat_rate = 0 # preserve',candidate.documents['settings.toml'])
            self.assertEqual(generation_config(store,base).input,InputConfig())
            self.assertFalse(store.root.exists())


class TouchpadSettingsTests(unittest.TestCase):
    def test_shared_defaults_and_distinct_mouse_scroll(self):
        expected={k.removeprefix('touchpad.'):v for k,v in DEFAULTS.items() if k.startswith('touchpad.')}
        self.assertEqual(asdict(TouchpadConfig()),expected)
        config=load_config_text('[input]\nnatural_scroll=false\nscroll_factor=0.5\n[touchpad]\nnatural_scroll=true\nscroll_factor=1.3')
        self.assertFalse(config.input.natural_scroll)
        self.assertTrue(config.touchpad.natural_scroll)
        self.assertEqual(config.touchpad.scroll_factor,1.3)
        self.assertEqual(config.input.scroll_factor,0.5)

    def test_invalid_values_do_not_coerce(self):
        for text in ('tap_to_click=1','scroll_factor=1','scroll_factor=nan','scroll_factor=2.1','drag_lock=true','drag_lock=3','drag_3fg=-1','tap_button_map="other"','unknown=true'):
            with self.subTest(text=text),self.assertRaises(ConfigError):load_config_text('[touchpad]\n'+text)

    def test_candidate_and_named_device_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            base=store._candidate({n:'schema_version = 1\n' for n in FILES})
            rules={'unplugged-touchpad':{'tap_to_click':False,'tap_and_drag':False,'drag_lock':2,'tap_button_map':'lmr','disable_while_typing':False}}
            changed=edit_candidate(store,base,{'touchpad.tap_to_click':False,'touchpad.scroll_factor':1.3,'devices':rules})
            config=generation_config(store,changed)
            self.assertFalse(config.touchpad.tap_to_click)
            self.assertEqual(config.touchpad.scroll_factor,1.3)
            self.assertEqual(config.devices,rules)
            self.assertEqual(generation_config(store,base).touchpad,TouchpadConfig())


class AbsoluteInputSettingsTests(unittest.TestCase):
    def test_defaults_and_sparse_vector_roundtrip(self):
        from luminophore_shell.config import TouchDeviceConfig, VirtualKeyboardConfig, TabletConfig, TabletToolConfig
        for group, cls in [('touchdevice',TouchDeviceConfig),('virtualkeyboard',VirtualKeyboardConfig),('tablet',TabletConfig),('tablettool',TabletToolConfig)]:
            self.assertEqual(asdict(cls()),{k.split('.',1)[1]:v for k,v in DEFAULTS.items() if k.startswith(group+'.')})
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            base=store._candidate({n:'schema_version = 1\n' for n in FILES})
            rules={'missing-tablet':{'region_position_x':0.3,'pressure_range_min':0.2,'output':'DP-9'}}
            changed=edit_candidate(store,base,{'tablet.region_position_y':0.7,'tablettool.pressure_range_max':0.9,'devices':rules})
            config=generation_config(store,changed)
            self.assertEqual(config.devices,rules)
            self.assertEqual(config.tablet.region_position_y,0.7)
            self.assertEqual(generation_config(store,base).tablet,TabletConfig())
            self.assertNotIn('region_position_y',config.devices['missing-tablet'])

    def test_invalid_global_and_inherited_device_values(self):
        cases=['[tablet]\nregion_size_x=1', '[tablet]\nactive_area_size_x=-0.1',
               '[touchdevice]\noutput="../DP-1"','[virtualkeyboard]\nshare_states=3',
               '[tablettool]\npressure_range_min=0.8\npressure_range_max=0.2',
               '[tablettool]\npressure_range_max=0.4\n[devices.pen]\npressure_range_min=0.8',
               '[devices.pen]\nenabled=false']
        for text in cases:
            with self.subTest(text=text),self.assertRaises(ConfigError):load_config_text(text)
        config=load_config_text('[tablettool]\npressure_range_min=0.8\npressure_range_max=-1.0')
        self.assertEqual(config.tablettool.pressure_range_max,-1.0)


class PointerSettingsTests(unittest.TestCase):
    def test_scroll_choices_have_display_labels_and_default(self):
        spec=next(s for s in specs_for_category('input') if s.path=='input.scroll_method')
        self.assertEqual([value for value,label in spec.choices],['','2fg','edge','on_button_down','no_scroll'])
        self.assertTrue(all(label for value,label in spec.choices))

    def test_defaults_restore_and_sparse_device_override(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            base=store._candidate({n:'schema_version = 1\n' for n in FILES})
            changes={'input.scroll_method':'on_button_down','input.scroll_button':274,'input.scroll_button_lock':True,'input.rotation':90,'devices':{'missing-pointer':{'scroll_method':'no_scroll','rotation':180}}}
            changed=edit_candidate(store,base,changes)
            config=generation_config(store,changed)
            self.assertEqual(config.input.scroll_button,274)
            self.assertEqual(config.devices,changes['devices'])
            reset=edit_candidate(store,changed,{k:DEFAULTS[k] for k in changes if k!='devices'}|{'devices':{}})
            self.assertEqual(generation_config(store,reset).input,InputConfig())
            self.assertEqual(generation_config(store,base).input,InputConfig())

    def test_invalid_method_button_rotation_and_types(self):
        for text in ('scroll_method="other"','scroll_button=301','scroll_button=true','scroll_button_lock=1','rotation=360','rotation=-1','rotation=1.0'):
            with self.subTest(text=text),self.assertRaises(ConfigError):load_config_text('[input]\n'+text)


class KeyboardPolicySettingsTests(unittest.TestCase):
    def test_policy_defaults_roundtrip_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            base=store._candidate({n:'schema_version = 1\n' for n in FILES})
            changes={'input.numlock_by_default':True,'input.resolve_binds_by_sym':True,'devices':{'missing-keyboard':{'numlock_by_default':False}}}
            changed=edit_candidate(store,base,changes);c=generation_config(store,changed)
            self.assertTrue(c.input.numlock_by_default);self.assertTrue(c.input.resolve_binds_by_sym)
            self.assertEqual(c.devices,changes['devices'])
            restored=edit_candidate(store,changed,{'input.numlock_by_default':False,'input.resolve_binds_by_sym':False,'devices':{}})
            self.assertEqual(generation_config(store,restored).input,InputConfig())
            self.assertEqual(generation_config(store,base).input,InputConfig())

    def test_policy_flags_reject_numeric_and_text_values(self):
        for key in ('numlock_by_default','resolve_binds_by_sym'):
            for value in ('1','"true"'):
                for section in ('input','devices.keyboard'):
                    with self.subTest(key=key,value=value,section=section),self.assertRaises(ConfigError):
                        load_config_text('['+section+']\n'+key+'='+value)


class FocusPolicySettingsTests(unittest.TestCase):
    def test_global_policy_roundtrip_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            base=store._candidate({n:'schema_version = 1\n' for n in FILES})
            changes={'input.follow_mouse':2,'input.follow_mouse_threshold':0.3,'input.mouse_refocus':False,'input.follow_mouse_shrink':12,'input.off_window_axis_events':0,'input.emulate_discrete_scroll':2}
            changed=edit_candidate(store,base,changes)
            values=settings_values(generation_config(store,changed))
            for key,value in changes.items():self.assertEqual(values[key],value)
            restored=edit_candidate(store,changed,{k:DEFAULTS[k] for k in changes})
            self.assertEqual(generation_config(store,restored).input,InputConfig())

    def test_global_only_policies_reject_device_rules_and_bad_types(self):
        keys=('follow_mouse','follow_mouse_threshold','mouse_refocus','follow_mouse_shrink','off_window_axis_events','emulate_discrete_scroll')
        for key in keys:
            value=repr(DEFAULTS['input.'+key]).lower()
            with self.subTest(key=key),self.assertRaises(ConfigError):load_config_text('[devices.mouse]\n'+key+'='+value)
        for text in ('follow_mouse=4','follow_mouse_threshold=nan','follow_mouse_threshold=1','mouse_refocus=1','follow_mouse_shrink=301','off_window_axis_events=-1','emulate_discrete_scroll=3'):
            with self.subTest(text=text),self.assertRaises(ConfigError):load_config_text('[input]\n'+text)


class FocusTransitionSettingsTests(unittest.TestCase):
    def test_all_choices_roundtrip_and_default_reset(self):
        keys=('input.focus_on_close','input.float_switch_override_focus')
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory);store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            base=store._candidate({n:'schema_version = 1\n' for n in FILES})
            for option in range(3):
                changed=edit_candidate(store,base,{k:option for k in keys})
                values=settings_values(generation_config(store,changed))
                for key in keys:self.assertEqual(values[key],option)
                restored=edit_candidate(store,changed,{k:DEFAULTS[k] for k in keys})
                self.assertEqual(generation_config(store,restored).input,InputConfig())
        for spec in specs_for_category('input'):
            if spec.path in keys:self.assertEqual([v for v,label in spec.choices],[0,1,2])

    def test_global_only_and_strict_enums(self):
        for key in ('focus_on_close','float_switch_override_focus'):
            for value in ('-1','3','true','1.0'):
                with self.subTest(key=key,value=value),self.assertRaises(ConfigError):load_config_text('[input]\n'+key+'='+value)
            with self.assertRaises(ConfigError):load_config_text('[devices.mouse]\n'+key+'=1')


class CustomAccelerationTests(unittest.TestCase):
    def test_curve_roundtrip_reset_and_sparse_device_inheritance(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory); store=settings_store(SettingsPaths(r/'c',r/'s',r/'cache'))
            base=store._candidate({n:'schema_version = 1\n' for n in FILES})
            patch={'input.accel_profile':'custom 0.5 0 1 3','input.scroll_points':'0.2 0 1',
                   'devices':{'unplugged-mouse':{'scroll_points':'0.3 0 2'}}}
            changed=edit_candidate(store,base,patch)
            config=generation_config(store,changed)
            self.assertEqual(config.input.accel_profile,patch['input.accel_profile'])
            self.assertEqual(config.devices,patch['devices'])
            reset=edit_candidate(store,changed,{'input.accel_profile':'','input.scroll_points':'','devices':{}})
            self.assertEqual(generation_config(store,reset).input,InputConfig())

    def test_malformed_curves_are_rejected_before_generation(self):
        import json
        invalid=(' custom 1 0 1','custom','custom 1 0','custom 0 0 1','custom -1 0 1','custom 1 -1 2',
                 'custom 1 nan 2','custom 1 0 inf','custom 1 0 1e999','custom 1 0 2junk',
                 'customish 1 0 1','custom\t1 0 1','custom 1 '+'0 '*65,'other','x'*4097)
        for value in invalid:
            for table in ('input','devices.mouse'):
                with self.subTest(value=value,table=table),self.assertRaises(ConfigError):
                    load_config_text('['+table+']\naccel_profile='+json.dumps(value))
        for text in ('[input]\nscroll_points="1 0 1"',
                     '[input]\naccel_profile="custom 1 0 1"\nscroll_points="1 0 1"\n[devices.mouse]\naccel_profile="flat"'):
            with self.subTest(text=text),self.assertRaises(ConfigError):load_config_text(text)

    def test_default_flat_adaptive_and_custom_profiles(self):
        import json
        for profile in ('','flat','adaptive','custom 1 0 2','custom +0.5 0 1e2'):
            self.assertEqual(load_config_text('[input]\naccel_profile='+json.dumps(profile)).input.accel_profile,profile)
