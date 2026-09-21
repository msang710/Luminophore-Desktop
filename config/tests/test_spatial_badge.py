from unittest.mock import Mock, patch
import unittest

from luminophore_shell.ui.spatial_badge import SpatialBadgeSurface
from luminophore_shell.ui.spatial_editor import SpatialEditorSurface
import test_spatial_grab_consumer as consumer

event = consumer.event


class SpatialBadgeTests(unittest.TestCase):
    def editor(self):
        editor, monitor = consumer.SpatialGrabConsumerTests().editor()
        self.addCleanup(editor._worker_cleanup)
        editor.monitor = monitor
        editor._show_badge = lambda e: SpatialEditorSurface._show_badge(editor, e)
        return editor, monitor

    @patch("luminophore_shell.ui.spatial_editor.SpatialBadgeSurface")
    def test_validated_begin_creates_one_asset_and_updates_do_not_recreate(self, factory):
        editor, monitor = self.editor()
        editor.handle_grab(event(), [], [monitor])
        factory.assert_not_called()
        editor._drain()
        factory.assert_called_once()
        badge = factory.return_value
        editor.handle_grab(event(phase="update", pointer=(15.5, 22.)), [])
        editor.handle_grab(event(phase="update", pointer=(-500., 1000.)), [])
        factory.assert_called_once()
        editor.handle_grab(event(phase="end", generation=99), [])
        badge.destroy.assert_not_called()
        editor.handle_grab(event(phase="end"), [])
        badge.destroy.assert_called_once()
        self.assertIsNone(editor._badge)

    @patch("luminophore_shell.ui.spatial_editor.SpatialBadgeSurface")
    def test_replacement_destroys_old_asset_and_late_end_preserves_new(self, factory):
        old, new = Mock(), Mock()
        factory.side_effect = [old, new]
        editor, monitor = self.editor()
        editor.handle_grab(event(), [], [monitor])
        editor._drain()
        editor.handle_grab(event(generation=2), [], [monitor])
        editor._drain()
        old.destroy.assert_called_once()
        self.assertIs(editor._badge, new)
        self.assertFalse(editor.handle_grab(event(phase="cancel"), []))
        new.destroy.assert_not_called()
        editor.close()
        new.destroy.assert_called_once()

    @patch("luminophore_shell.ui.spatial_editor.SpatialBadgeSurface")
    def test_early_release_never_creates_late_badge(self, factory):
        editor, monitor = self.editor()
        editor.handle_grab(event(), [], [monitor])
        editor.handle_grab(event(phase="end"), [])
        editor._drain()
        factory.assert_not_called()

    @patch("luminophore_shell.ui.spatial_editor.SpatialBadgeSurface")
    def test_close_reset_and_cancel_destroy_asset(self, factory):
        for action in (lambda e: e.close(), lambda e: e.connection_reset(), lambda e: e.handle_grab(event(phase="cancel"), [])):
            factory.reset_mock()
            editor, monitor = self.editor()
            editor.handle_grab(event(), [], [monitor])
            editor._drain()
            action(editor)
            factory.return_value.destroy.assert_called_once()
            self.assertIsNone(editor._badge)

    @patch("luminophore_shell.ui.spatial_badge.HyprlandClient")
    @patch("luminophore_shell.ui.spatial_badge.Gtk.ApplicationWindow")
    def test_asset_uses_native_fallback_without_gtk_window(self, window_type, client_type):
        client_type.return_value.native_command.return_value = 'true'
        monitor = Mock()
        monitor.get_connector.return_value = 'DP-1'
        badge = SpatialBadgeSurface(Mock(), monitor, event(), None, None, 0)
        window_type.assert_not_called()
        call = client_type.return_value.native_command.call_args
        self.assertEqual(call.args[0], 'grab-badge')
        self.assertEqual(call.kwargs['generation'], '1')
        self.assertEqual(call.kwargs['mask'], '')
        self.assertTrue(badge.sent)
        client_type.return_value.native_command.reset_mock()
        badge.destroy()
        client_type.return_value._run.assert_not_called()
