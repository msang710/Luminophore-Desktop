from pathlib import Path
import tempfile
import unittest

from luminophore_shell.config import ConfigError, load_config_text
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_generation import edit_candidate, generation_config
from luminophore_shell.settings_schema import settings_values, specs_for_category
from luminophore_shell.settings_store import FILES, SettingsPaths

VALUES = {
    'compositor.pointer_focus_output': False,
    'compositor.fullscreen_focus_policy': 1,
    'compositor.fullscreen_after_close': True,
}


class FocusSettingsTests(unittest.TestCase):
    def test_editable_render_controls_persist_and_restore_without_changing_visual_preset(self):
        available = {spec.path for spec in specs_for_category('compositor')}
        self.assertTrue(set(VALUES) <= available, set(VALUES) - available)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = settings_store(SettingsPaths(root/'config', root/'state', root/'cache'))
            baseline = store._candidate({name: 'schema_version = 1\n' for name in FILES})
            before = generation_config(store, baseline)
            candidate = edit_candidate(store, baseline, VALUES)
            after = generation_config(store, candidate)
            actual = settings_values(after)
            for key, value in VALUES.items():
                self.assertEqual(actual[key], value)
            self.assertEqual(before.visual, after.visual)
            self.assertEqual(before.input, after.input)
            restored = edit_candidate(store, candidate, {key: settings_values(before)[key] for key in VALUES})
            self.assertEqual(generation_config(store, restored).compositor, before.compositor)
            self.assertEqual(generation_config(store, baseline).compositor, before.compositor)

    def test_invalid_focus_values_are_rejected_without_coercion(self):
        for key in ('pointer_focus_output','fullscreen_after_close'):
            for value in ('1', '0', '"true"', '0.5', '[]'):
                with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                    load_config_text('[compositor]\n' + key + '=' + value)
        for value in ('-1','3','true','1.0','"1"'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load_config_text('[compositor]\nfullscreen_focus_policy=' + value)

    def test_defaults_and_all_fullscreen_modes(self):
        current = settings_values(load_config_text(''))
        self.assertTrue(current['compositor.pointer_focus_output'])
        self.assertEqual(current['compositor.fullscreen_focus_policy'], 2)
        self.assertFalse(current['compositor.fullscreen_after_close'])
        for mode in range(3):
            value = load_config_text('[compositor]\nfullscreen_focus_policy=' + str(mode))
            self.assertEqual(value.compositor.fullscreen_focus_policy, mode)
