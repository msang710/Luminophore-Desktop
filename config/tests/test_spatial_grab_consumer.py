from dataclasses import replace
from unittest.mock import Mock, patch
import unittest
from types import SimpleNamespace

from luminophore_shell.spatial_editor import SpatialEditorMode, SpatialEditorState
from luminophore_shell.spatial_grab import SpatialGrabEvent
from test_spatial_drag import snapshot, result
import test_spatial_editor_input as input_fixture


def event(**changes):
    return replace(SpatialGrabEvent('source', 'begin', 1, 3, 2, 10, '0xa', False, None), **changes)


class SpatialGrabConsumerTests(unittest.TestCase):
    def test_identity_and_preview_revision_must_match_original_grab(self):
        state = SpatialEditorState()
        self.assertTrue(state.accept_grab(event(), snapshot()))
        for changes in ({'generation': 2}, {'source': 'other'}, {'output_id': 20},
                        {'window': '0xb'}, {'floating': True}, {'revision': 4},
                        {'result': result(revision=5)}, {'result': result(topology=3)}):
            with self.subTest(changes=changes):
                self.assertFalse(state.accept_grab(event(phase='update', **changes)))
                self.assertEqual(state.grab_generation, 1)
        self.assertTrue(state.accept_grab(event(phase='update', result=result())))
        self.assertTrue(state.accept_grab(event(phase='end')))
        self.assertFalse(state.accept_grab(event(phase='end')))
        self.assertFalse(state.visible)

    def test_rejected_begin_preserves_current_snapshot_and_generation(self):
        state = SpatialEditorState()
        state.accept_grab(event(), snapshot())
        newer = replace(snapshot(), revision=4, committed_model_revision=4)
        self.assertFalse(state.accept_grab(event(revision=4), newer))
        self.assertEqual(state.view.state, snapshot())
        for changes in ({'window': '0xff'}, {'output_id': 30}, {'floating': True}):
            self.assertFalse(state.accept_grab(event(generation=2, **changes), snapshot()))
        self.assertEqual(state.grab_generation, 1)

    def editor(self):
        editor = input_fixture.SpatialEditorInputTests().editor()
        self.addCleanup(editor._worker_cleanup)
        editor.window = Mock()
        editor._visible_cells = Mock(return_value=())
        editor.set_monitor = Mock()
        editor.client.spatial_state.return_value = replace(snapshot(), output_names=((10, 'DP-1'),))
        monitor = Mock()
        monitor.get_connector.return_value = 'DP-1'
        return editor, monitor

    def test_transient_uses_compositor_preview_and_never_shell_commit(self):
        editor, monitor = self.editor()
        self.assertTrue(editor.handle_grab(event(), [], [monitor]))
        editor._drain()
        self.assertEqual(editor.state.mode, SpatialEditorMode.TRANSIENT_DRAG)
        editor._drag_begin(Mock(), 60, 65)
        self.assertFalse(editor.state.edits.active)
        self.assertTrue(editor.handle_grab(event(phase='update', result=result()), []))
        editor.preview_panel.update.assert_called_once()
        revision = editor._revision
        editor.handle_grab(event(phase='update'), [])
        self.assertGreater(editor._revision, revision)
        self.assertTrue(editor.handle_grab(event(phase='end'), []))
        self.assertEqual(editor.state.mode, SpatialEditorMode.PERSISTENT)
        editor.client.spatial_preview.assert_not_called()
        editor.client.spatial_commit.assert_not_called()

    def test_close_and_missing_output_do_not_reopen_or_replace_grab(self):
        editor, monitor = self.editor()
        self.assertFalse(editor.handle_grab(event(), [], []))
        self.assertEqual(editor.state.mode, SpatialEditorMode.PERSISTENT)
        editor.handle_grab(event(), [], [monitor])
        editor.close()
        editor._drain()
        self.assertFalse(editor.handle_grab(event(phase='end'), []))
        self.assertFalse(editor.handle_grab(event(phase='update', result=result()), []))
        self.assertFalse(editor.visible)

    def test_listener_routes_grab_only_through_main_loop(self):
        from luminophore_shell.app import LuminophoreShellApplication
        app = SimpleNamespace()
        app._handle_spatial_grab = Mock()
        app._queue_window_refresh = Mock()
        with patch('luminophore_shell.app.GLib.idle_add') as idle:
            LuminophoreShellApplication._hypr_event(app, 'luminophorespatialgrab', 'payload')
            idle.assert_called_once_with(app._handle_spatial_grab, 'payload')
        app._handle_spatial_grab.assert_not_called()

    def test_end_before_snapshot_completion_cannot_reopen_transient(self):
        editor, monitor = self.editor()
        editor.handle_grab(event(), [], [monitor])
        self.assertTrue(editor.handle_grab(event(phase="end"), []))
        editor._drain()
        self.assertEqual(editor.state.mode, SpatialEditorMode.PERSISTENT)
        editor.window.present.assert_not_called()
        editor.client.spatial_commit.assert_not_called()

    def test_early_terminal_releases_both_press_owners(self):
        from luminophore_shell.spatial_preview import SpatialNativeDrag
        for phase in ('end', 'cancel'):
            editor, monitor = self.editor()
            editor._native_drag = SpatialNativeDrag(editor.client, lambda f: None, Mock())
            self.addCleanup(editor._native_drag.worker.close)
            editor._native_press = editor._native_drag.press = 1234
            editor.handle_grab(event(editor_origin=True), [], [monitor])
            self.assertTrue(editor.handle_grab(event(phase=phase, editor_origin=True), []))
            self.assertIsNone(editor._native_press)
            self.assertIsNone(editor._native_drag.press)
            self.assertTrue(editor._native_drag.begin('0xa', snapshot(), 10, 1235))
            editor._drain()
            editor.window.present.assert_not_called()

    def test_old_terminal_cannot_clear_new_pending_press(self):
        editor, monitor = self.editor()
        editor.handle_grab(event(), [], [monitor])
        editor._drain()
        editor.handle_grab(event(generation=2), [], [monitor])
        editor._native_press = 1235
        self.assertFalse(editor.handle_grab(event(phase='end'), []))
        self.assertEqual(editor._native_press, 1235)
        editor._native_drag.finished.assert_not_called()

    def test_latest_native_update_is_kept_until_async_begin_is_validated(self):
        editor, monitor = self.editor()
        editor.handle_grab(event(), [], [monitor])
        editor.handle_grab(event(phase="update", result=result()), [])
        editor._drain()
        self.assertEqual(editor.state.mode, SpatialEditorMode.TRANSIENT_DRAG)
        editor.preview_panel.update.assert_called_once()

class SpatialGrabRetargetTests(unittest.TestCase):
    def test_target_changes_keep_source_identity_and_reject_old_epoch(self):
        state = SpatialEditorState()
        original = event(target_output_id=10)
        self.assertTrue(state.accept_grab(original, snapshot()))
        changed = event(phase='update', target_output_id=20, target_epoch=1)
        self.assertTrue(state.accept_grab(changed))
        self.assertEqual(state.grab_event.output_id, 10)
        self.assertEqual(state.grab_event.target_output_id, 20)
        self.assertFalse(state.accept_grab(event(phase='update', target_output_id=10)))
        self.assertFalse(state.accept_grab(event(phase='update', target_output_id=10, target_epoch=1)))
        self.assertTrue(state.accept_grab(event(phase='update', target_output_id=0, target_epoch=2)))
        self.assertTrue(state.accept_grab(event(phase='end', target_output_id=0, target_epoch=2)))

    def test_editor_origin_preserves_viewport_and_does_not_replay_reveal(self):
        editor, monitor = SpatialGrabConsumerTests().editor()
        self.addCleanup(editor._worker_cleanup)
        editor._editor_origin = (-4, -3)
        self.assertTrue(editor.handle_grab(event(editor_origin=True), [], [monitor]))
        editor._drain()
        self.assertEqual(editor._editor_origin, (-4, -3))
        editor.frame.animate.assert_not_called()
        self.assertEqual(editor.state.mode, SpatialEditorMode.TRANSIENT_DRAG)
        self.assertTrue(editor.handle_grab(event(phase='end', editor_origin=True), []))
        self.assertEqual(editor.state.mode, SpatialEditorMode.PERSISTENT)
