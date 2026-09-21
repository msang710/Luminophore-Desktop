from dataclasses import replace
from unittest.mock import Mock, patch
from types import SimpleNamespace
import unittest

from luminophore_shell.hyprland import HyprlandError
from luminophore_shell.ui.spatial_editor import SpatialEditorSurface
import test_spatial_grab_consumer as consumer


class SpatialGrabPresentationTests(unittest.TestCase):
    def editor(self):
        editor, monitor = consumer.SpatialGrabConsumerTests().editor()
        self.addCleanup(editor._worker_cleanup)
        editor._visible_cells.return_value = ((1, 1, 20.5, 40., 45., 30.),)
        editor.window.get_width.return_value = 700
        editor.window.get_height.return_value = 350
        editor._allocation = (700, 350)
        editor.handle_grab(consumer.event(), [], [monitor])
        editor._drain()
        editor.client.shell_projection.return_value = True
        editor.client.spatial_grab_layout.return_value = True
        editor._submit_projection()
        frame = editor._projection_frame
        return editor, f'luminophore-shell-spatial-editor,{frame[0]},9007199254740993,{frame[1]}'

    def test_commit_ack_does_not_register_and_presentation_uses_protocol_revision(self):
        editor, payload = self.editor()
        editor.client.spatial_grab_layout.assert_not_called()
        self.assertTrue(editor.window.get_surface().set_input_region.call_args.args[0].is_empty())
        self.assertTrue(editor.projection_presented(payload))
        layout = editor.client.spatial_grab_layout.call_args.args[0]
        self.assertEqual(layout.frame_revision, 9007199254740993)
        self.assertEqual((layout.generation, layout.revision, layout.topology_revision), (1, 3, 2))
        self.assertEqual(layout.cells, editor._visible_cells())
        self.assertFalse(editor.projection_presented(payload))
        editor.client.spatial_grab_layout.assert_called_once()

    def test_stale_namespace_generation_content_and_malformed_events_are_ignored(self):
        editor, payload = self.editor()
        fields = payload.split(',')
        for index, value in ((0, 'another-surface'), (1, 'old-frame'), (2, '-1'), (2, str(2**64)), (2, 'x'), (3, '0')):
            changed = fields.copy()
            changed[index] = value
            self.assertFalse(editor.projection_presented(','.join(changed)))
        self.assertFalse(editor.projection_presented('broken'))
        editor.client.spatial_grab_layout.assert_not_called()
        self.assertTrue(editor.projection_presented(payload))

    def test_geometry_changed_before_presentation_cannot_register_stale_cells(self):
        editor, payload = self.editor()
        editor._visible_cells.return_value = ((1, 1, 0., 0., 10., 10.),)
        self.assertFalse(editor.projection_presented(payload))
        editor.client.spatial_grab_layout.assert_not_called()
        self.assertIsNone(editor._projection_frame)
        editor._handshake.start.assert_called()

    def test_scroll_invalidates_registered_layout_and_close_removes_watcher(self):
        editor, payload = self.editor()
        editor.projection_presented(payload)
        original = editor._registered_layout
        editor._visible_cells.return_value = ()
        self.assertTrue(editor._watch_grab_geometry(None, None))
        self.assertEqual(editor.client.spatial_grab_layout.call_args.args[0], replace(original, cells=()))
        self.assertIsNone(editor._registered_layout)
        editor.close()
        editor.window.remove_tick_callback.assert_called_once()
        self.assertFalse(editor.projection_presented(payload))

    def test_rejected_or_ambiguous_registration_is_consumed_without_retry(self):
        for outcome in (False, HyprlandError('timeout')):
            editor, payload = self.editor()
            if isinstance(outcome, Exception):
                editor.client.spatial_grab_layout.side_effect = outcome
            else:
                editor.client.spatial_grab_layout.return_value = outcome
            self.assertFalse(editor.projection_presented(payload))
            self.assertFalse(editor.projection_presented(payload))
            editor.client.spatial_grab_layout.assert_called_once()

    def test_ambiguous_registration_is_invalidated_on_close(self):
        editor, payload = self.editor()
        editor.client.spatial_grab_layout.side_effect = [HyprlandError('timeout'), True]
        self.assertFalse(editor.projection_presented(payload))
        attempted = editor._registered_layout
        self.assertIsNotNone(attempted)
        editor.close()
        self.assertEqual(editor.client.spatial_grab_layout.call_args.args[0], replace(attempted, cells=()))
        self.assertEqual(editor.client.spatial_grab_layout.call_count, 2)

    def test_cells_are_clipped_to_viewport_and_window_in_window_coordinates(self):
        editor = object.__new__(SpatialEditorSurface)
        editor.scroll, editor.window, editor.panel = Mock(), Mock(), Mock()
        editor.window.get_width.return_value = 80
        editor.window.get_height.return_value = 90
        editor.scroll.get_child.return_value = (10., 20., 100., 60.)
        editor.panel.holders = {(0, 0): (-5., 10., 30., 30.), (1, 0): (70., 70., 30., 30.), (2, 0): (100., 20., 10., 10.)}
        editor._bounds = lambda widget, target: widget
        self.assertEqual(editor._visible_cells(), ((0, 0, 10., 20., 15., 20.), (1, 0, 70., 70., 10., 10.)))

    def test_presentation_matching_waits_until_commit_ack_is_recorded_on_main_loop(self):
        from luminophore_shell.app import LuminophoreShellApplication
        app = SimpleNamespace(hyprland=Mock(), _spatial_editor_presented=Mock(), spatial_editor_surface=Mock())
        with patch('luminophore_shell.app.GLib.idle_add') as idle:
            LuminophoreShellApplication._hypr_event(app, 'luminophoreshellpresented', 'payload')
            idle.assert_called_once_with(app._spatial_editor_presented, 'payload')
            app.hyprland.shell_projection_presented.assert_not_called()
        for known in (False, True):
            app.spatial_editor_surface.reset_mock()
            app.hyprland.shell_projection_presented.return_value = known
            self.assertFalse(LuminophoreShellApplication._spatial_editor_presented(app, 'payload'))
            if known:
                app.spatial_editor_surface.projection_presented.assert_called_once_with('payload')
            else:
                app.spatial_editor_surface.projection_presented.assert_not_called()
