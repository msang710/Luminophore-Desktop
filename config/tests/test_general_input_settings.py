from pathlib import Path
import tempfile
import unittest
from luminophore_shell.config import ConfigError, load_config_text
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_generation import edit_candidate, generation_config
from luminophore_shell.settings_store import SettingsPaths, FILES
from luminophore_shell.input_settings import decode_devices

VALUES = {'drag_threshold': 23, 'scroll_event_delay': 23, 'cursor_inactive_timeout': 1.3, 'cursor_no_warps': True, 'cursor_persistent_warps': True, 'cursor_hide_on_key_press': True, 'cursor_hide_on_touch': False, 'cursor_hide_on_tablet': True, 'cursor_warp_back_after_non_mouse_input': True, 'resize_on_border': True, 'extend_border_grab_area': 23, 'resize_on_border_inner_area': 23, 'hover_icon_on_border': False, 'resize_corner': 2, 'close_gesture_timeout': 550}

class GeneralInputSettingsTests(unittest.TestCase):
    def test_sparse_toml_candidate_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            store=settings_store(SettingsPaths(root/'c',root/'s',root/'cache'))
            original=store._candidate({name:'schema_version = 1\n' for name in FILES})
            baseline=generation_config(store,original)
            changed=edit_candidate(store,original,{'input.'+key:value for key,value in VALUES.items()})
            actual=generation_config(store,changed)
            for key,value in VALUES.items():
                self.assertEqual(getattr(actual.input,key),value)
            restored=edit_candidate(store,changed,{'input.'+key:getattr(baseline.input,key) for key in VALUES})
            self.assertEqual(generation_config(store,restored).input,baseline.input)
            self.assertEqual(generation_config(store,original).input,baseline.input)

    def test_invalid_fields_do_not_coerce(self):
        for assignment in ('drag_threshold=true','drag_threshold=-1','scroll_event_delay=2001',
                           'cursor_inactive_timeout=1','cursor_inactive_timeout=nan',
                           'cursor_inactive_timeout=-0.1','cursor_inactive_timeout=20.1',
                           'cursor_no_warps=1','resize_corner=5','resize_on_border_inner_area=101',
                           'close_gesture_timeout=9'):
            with self.subTest(assignment=assignment),self.assertRaises(ConfigError):
                load_config_text('[input]\n'+assignment)

    def test_global_settings_rejected_in_device_rules(self):
        for key,value in VALUES.items():
            with self.subTest(key=key),self.assertRaises(ValueError):
                decode_devices({'pointer':{key:value}})
