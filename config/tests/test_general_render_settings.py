from pathlib import Path
import tempfile
import unittest

from luminophore_shell.config import ConfigError, load_config_text
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_generation import edit_candidate, generation_config
from luminophore_shell.settings_schema import settings_values, specs_for_category
from luminophore_shell.settings_store import FILES, SettingsPaths

VALUES = {
    'compositor.fullscreen_opacity': 0.73,
    'compositor.dim_inactive': True,
    'compositor.dim_modal': False,
    'compositor.dim_strength': 0.31,
    'compositor.dim_around': 0.27,
    'compositor.blur_popups': True,
    'compositor.blur_input_methods': True,
    'compositor.render_unfocused_fps': 37,
}


class GeneralRenderSettingsTests(unittest.TestCase):
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

    def test_invalid_render_values_are_rejected_without_coercion(self):
        for assignment in ('fullscreen_opacity=1', 'fullscreen_opacity=nan', 'fullscreen_opacity=1.1',
                           'dim_strength=-0.1', 'dim_around=1.1', 'dim_modal=1',
                           'dim_inactive="true"', 'blur_popups=1', 'blur_input_methods="false"',
                           'render_unfocused_fps=0', 'render_unfocused_fps=121'):
            with self.subTest(assignment=assignment), self.assertRaises(ConfigError):
                load_config_text('[compositor]\n' + assignment)

    def test_zero_opacity_and_full_dim_are_valid_explicit_values(self):
        config = load_config_text('[compositor]\nfullscreen_opacity=0.0\ndim_strength=1.0\ndim_around=0.0\nrender_unfocused_fps=1')
        current = settings_values(config)
        self.assertEqual(current['compositor.fullscreen_opacity'], 0.0)
        self.assertEqual(current['compositor.dim_strength'], 1.0)
        self.assertEqual(current['compositor.dim_around'], 0.0)
        self.assertEqual(current['compositor.render_unfocused_fps'], 1)
