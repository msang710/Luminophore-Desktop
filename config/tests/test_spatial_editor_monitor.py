from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from luminophore_shell.app import LuminophoreShellApplication

class EditorMonitorTests(unittest.TestCase):
    @patch('luminophore_shell.app.Gdk.Display.get_default', return_value=Mock())
    def test_connector_selection_uses_compositor_coordinates_and_scale(self, _display):
        left, right = Mock(), Mock()
        left.get_connector.return_value = 'DP-1'
        right.get_connector.return_value = 'DP-2'
        editor = Mock(visible=False)
        client = Mock()
        client.cursor_position.return_value = (2000, 200)
        client.query.return_value = [dict(name='DP-1',x=0,y=0,width=3840,height=2160,scale=2),
                                    dict(name='DP-2',x=1920,y=0,width=1920,height=1080,scale=1)]
        app = SimpleNamespace(spatial_editor_surface=editor,hyprland=client,
                              _monitors=lambda _: [right,left],osd_layers={},windows=[])
        self.assertTrue(LuminophoreShellApplication._toggle_spatial_editor(app))
        editor.set_monitor.assert_called_once_with(right)
        left.get_geometry.assert_not_called()
        right.get_geometry.assert_not_called()
        editor.toggle.assert_called_once_with([])
