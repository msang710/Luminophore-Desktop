import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4

from luminophore_shell.config import ConfigError
from luminophore_shell.hyprland import HyprlandError
from luminophore_shell.settings_contract import SettingsCompletionUnknown
from luminophore_shell.visual_settings import VisualSettings, VisualSettingsState


def application_methods():
    # Execute the actual application methods without initializing GTK/services.
    path = Path("luminophore_shell/app.py")
    source = ast.parse(path.read_text())
    owner = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "LuminophoreShellApplication")
    selected = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name in {
        "_resync_visual_settings", "_verify_visual_config", "_visual_renderer_available"}]
    owner.body = selected
    owner.bases = []
    owner.decorator_list = []
    namespace = {"ShellConfig": object, "ConfigError": ConfigError, "HyprlandError": HyprlandError,
                 "SettingsCompletionUnknown": SettingsCompletionUnknown, "uuid4": uuid4, "config_mtime_ns": lambda path: 42}
    exec(compile(ast.Module(body=[owner], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["LuminophoreShellApplication"]()


class VisualRuntimeApplicationTests(unittest.TestCase):
    def test_capability_is_read_from_renderer_generation(self):
        app = application_methods()
        for schema in (0, 1):
            app.hyprland = SimpleNamespace(visual_state=lambda: SimpleNamespace(renderer_schema=schema, source="a" * 32))
            self.assertEqual(app._visual_renderer_available(), schema == 1)
        def unavailable():
            raise HyprlandError("unavailable")
        app.hyprland = SimpleNamespace(visual_state=unavailable)
        self.assertFalse(app._visual_renderer_available())

    def test_reconnect_has_no_independent_visual_writer(self):
        app = application_methods()
        def forbidden(*args): raise AssertionError('independent settings mutation')
        app.hyprland = SimpleNamespace(apply_visual_settings=forbidden)
        for _ in range(2): self.assertFalse(app._resync_visual_settings())
        self.assertFalse(hasattr(app, '_apply_visual_config'))

    def test_verification_reads_compositor_not_only_local_config(self):
        app = application_methods()
        desired = VisualSettings(enabled=False)
        state = VisualSettingsState(2, True, "none", desired, 1)
        app.hyprland = SimpleNamespace(visual_state=lambda: state)
        app._verify_visual_config(SimpleNamespace(visual=desired))
        with self.assertRaises(SettingsCompletionUnknown):
            app._verify_visual_config(SimpleNamespace(visual=VisualSettings()))
