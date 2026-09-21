import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

class ProductionSettingsPaths(TestCase):
    def test_portable_shell_siblings_live_in_config_modules(self):
        from luminophore_shell.config_paths import lua_config_path
        with patch.dict(os.environ, {'LUMINOPHORE_CONFIG_ROOT': '/tmp/portable'}):
            self.assertEqual(lua_config_path('bindings', Path('/tmp/portable/shell.toml')),
                             Path('/tmp/portable/config/luminophore_bindings.lua'))

    def test_source_layout_keeps_existing_siblings(self):
        from luminophore_shell.config_paths import lua_config_path
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(lua_config_path('theme', Path('/tmp/source/luminophore_shell/config.toml')),
                             Path('/tmp/source/luminophore_theme.lua'))

    def test_lua_bridge_resolves_portable_root_first(self):
        text = Path('luminophore_settings.lua').read_text()
        self.assertIn('os.getenv("LUMINOPHORE_CONFIG_ROOT")', text)
        self.assertIn('root .. "/shell.toml"', text)
