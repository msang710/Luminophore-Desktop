from dataclasses import replace
from unittest.mock import Mock, patch
import unittest

from luminophore_shell.spatial_editor import SpatialEditorState
from luminophore_shell.ui.spatial_editor import SpatialEditorSurface
from test_spatial_drag import snapshot, result
from test_spatial_preview import PreviewHarness


class SpatialEditorInputTests(unittest.TestCase):
    def test_projection_blurs_only_view_bounds_in_window_coordinates(self):
        class Visual:
            def geometry(self):
                return (0,0,500,400), ((40,50,116,174),)
        editor = self.editor()
        self.addCleanup(editor._worker_cleanup)
        editor.frame = Visual()
        editor.window.get_width.return_value = 800
        editor.window.get_height.return_value = 700
        editor._bounds = lambda widget, target: (10,20,500,400)
        with patch('luminophore_shell.ui.spatial_editor.EditorVisual', Visual), patch('luminophore_shell.ui.spatial_editor.submit_surface_projection') as submit:
            editor._submit_projection()
        style = submit.call_args.args[2][4]
        self.assertEqual(tuple(style[k] for k in ('blur_x','blur_y','blur_width','blur_height')), (50,70,116,174))
        self.assertEqual((style['panel_width'],style['panel_height']), (500,400))

    def test_presented_editor_flushes_input_without_another_show_or_content_change(self):
        editor = object.__new__(SpatialEditorSurface)
        editor.frame = Mock()
        editor._badge = None
        editor._show_badge = Mock()
        editor.state = SpatialEditorState()
        editor.state.toggle()
        editor.window = Mock()
        editor.window.get_width.return_value = 734
        editor.window.get_height.return_value = 342
        surface = editor.window.get_surface.return_value
        events = []
        surface.set_input_region.side_effect = lambda region: events.append(("input", region))
        surface.queue_render.side_effect = lambda: events.append(("render", None))

        editor._projection_applied(True)

        self.assertEqual([event[0] for event in events], ["input", "render"])
        self.assertTrue(events[0][1].contains_point(733, 341))
        editor.state.close()
        events.clear()
        editor._projection_applied(True)
        self.assertEqual([event[0] for event in events], ["input", "render"])
        self.assertTrue(events[0][1].is_empty())

    def editor(self):
        editor = object.__new__(SpatialEditorSurface)
        editor.window = Mock()
        editor.frame = Mock()
        editor._badge = None
        editor._show_badge = Mock()
        editor.state = SpatialEditorState()
        editor.state.toggle()
        editor.state.accept(snapshot())
        editor._native_drag = Mock()
        editor._native_drag.pending = False
        editor._native_press = None
        editor.client = Mock()
        editor.client.spatial_state.return_value = snapshot()
        editor.client.spatial_preview.return_value = result()
        editor.client.spatial_commit.return_value = result()
        editor.panel = Mock()
        editor.panel.holders = {(x, y): (x * 50., y * 50., 49., 49.) for x in range(15) for y in range(5)}
        editor.panel.icon_widgets = {"0xa": Mock(bounds=(55., 60., 15., 15.)), "0xb": Mock(bounds=(75., 60., 15., 15.))}
        editor._bounds = lambda widget, target: widget if isinstance(widget, tuple) else widget.bounds
        editor.preview_panel = Mock()
        editor.status, editor.outputs = Mock(), Mock()
        editor.palette_indices={}
        editor.board_tabs=Mock()
        editor.board_tabs.get_first_child.return_value=None
        editor._board_buttons={}
        editor.board_selector=Mock()
        editor._board_output=0
        editor._editor_origin=None
        editor._handshake = Mock(pending=False)
        editor._windows = []
        editor._revision = 1
        editor._shown_preview = None
        editor._drag_cells = {}
        editor._pending_grab = None
        editor._pending_grab_update = None
        harness = PreviewHarness(editor.state.edits, editor.client, editor._preview_changed, editor._finish_drag)
        editor._preview = harness.controller
        editor._drain = harness.drain
        editor._worker_cleanup = harness.close
        self.addCleanup(harness.close)
        return editor

    def test_icons_in_same_cell_delegate_once_without_shell_commit(self):
        for x, address in ((60, "0xa"), (80, "0xb")):
            editor = self.editor()
            gesture = Mock()
            gesture.get_current_event_time.return_value = 1234
            editor._drag_begin(gesture, x, 65)
            editor._native_drag.begin.assert_called_once_with(address, snapshot(), 10, 1234)
            self.assertFalse(editor.state.edits.active)
            editor._drag_update(None, 100, 50)
            editor._drag_cancel(None, None)  # Native pointer takeover cancels GTK.
            editor._drag_end(None, 100, 50)
            editor._drain()
            editor.client.spatial_commit.assert_not_called()
            editor._native_drag.cancel.assert_not_called()

    def test_cell_padding_moves_app_and_empty_interior_is_inert(self):
        editor = self.editor()
        self.assertEqual(editor._interaction_at(52, 90)[0], "move-window")
        self.assertIsNone(editor._interaction_at(175, 25))

    def test_view_interior_and_corner_choose_distinct_actions(self):
        editor = self.editor()
        editor._drag_begin(Mock(), 224, 10)
        self.assertEqual(editor.state.edits.drag.action, "move-view")
        self.assertEqual(editor.state.edits.drag.output_id, 20)
        editor._drag_cancel(None, None)
        editor._drag_begin(Mock(), 151, 1)
        self.assertEqual(editor.state.edits.drag.action, "resize-view")
        self.assertEqual(editor.state.edits.drag.edges, ("left", "top"))

    def test_outside_drop_and_cancel_do_not_commit(self):
        editor = self.editor()
        editor._drag_begin(Mock(), 74, 10)
        editor._drag_update(None, 50, 0)
        editor._drag_end(None, -1000, 0)
        editor.client.spatial_commit.assert_not_called()
        editor._drag_begin(Mock(), 74, 10)
        editor._drag_cancel(None, None)
        editor._drag_end(None, 50, 0)
        editor.client.spatial_commit.assert_not_called()
        self.assertFalse(editor.state.edits.active)

    def test_same_revision_refresh_keeps_widgets_new_revision_cancels(self):
        editor = self.editor()
        editor._drag_begin(Mock(), 74, 10)
        editor.update([])
        editor._drain()
        editor.panel.update.assert_not_called()
        newer = replace(snapshot(), revision=4, committed_model_revision=4)
        editor.client.spatial_state.return_value = newer
        editor.update([])
        editor._drain()
        editor.panel.update.assert_called_once_with(newer, [])
        editor._drag_end(None, 50, 0)
        editor.client.spatial_commit.assert_not_called()

    def test_stale_preview_refreshes_and_never_replays_release(self):
        editor = self.editor()
        editor._drag_begin(Mock(), 74, 10)
        editor.client.spatial_preview.return_value = result("stale-topology", 4, 3)
        editor._drag_update(None, 50, 0)
        editor._drain()
        editor.client.spatial_state.assert_called_once()
        editor._drag_end(None, 50, 0)
        editor.client.spatial_commit.assert_not_called()

    def test_preview_uses_solver_placements_and_does_not_rebuild_for_same_candidate(self):
        editor = self.editor()
        editor._drag_begin(Mock(), 74, 10)
        preview = replace(result(), windows=(("0xa", 3, 2, "tiled"), ("0xb", 1, 1, "floating")),
                          output_views=((10, 0, 0, 3, 2), (20, 4, 0, 3, 2)))
        editor._render_preview(preview)
        editor._render_preview(preview)
        candidate = editor.preview_panel.update.call_args.args[0]
        self.assertEqual((candidate.windows[0].x, candidate.windows[0].y), (3, 2))
        self.assertEqual(candidate.output_views[1].rect.x, 4)
        self.assertEqual(editor.preview_panel.update.call_count, 1)
        self.assertEqual(editor.state.view.state, snapshot())

    def test_allocation_change_cancels_and_rejected_projection_disables_commit(self):
        editor = self.editor()
        editor.window = Mock()
        editor.window.get_width.return_value = 750
        editor.window.get_height.return_value = 350
        editor._allocation = (700, 350)
        editor._drag_begin(Mock(), 74, 10)
        editor._frame_revision()
        self.assertFalse(editor.state.edits.active)
        editor._drag_end(None, 50, 0)
        editor.client.spatial_commit.assert_not_called()
        editor._drag_begin(Mock(), 74, 10)
        editor.client.shell_projection.return_value = False
        editor._submit_projection()
        self.assertFalse(editor.state.edits.active)
        editor._drag_end(None, 50, 0)
        editor.client.spatial_commit.assert_not_called()

    def test_cursor_tracks_the_same_action_as_drag(self):
        editor = self.editor()
        self.addCleanup(editor._worker_cleanup)
        editor._pointer_motion(None, 74, 10)
        editor.panel.set_cursor_from_name.assert_called_with('grab')
        editor._drag_begin(Mock(), 74, 10)
        editor.panel.set_cursor_from_name.assert_called_with('grabbing')
        editor.window.set_cursor_from_name.assert_called_with('grabbing')
        self.assertEqual(editor.window.get_surface().set_cursor.call_args.args[0].get_name(), 'grabbing')
        editor._pointer_motion(None, -10, -10)
        editor.panel.set_cursor_from_name.assert_called_with('no-drop')
        editor._drag_cancel(Mock(), None)
        editor._pointer_leave(None)
        editor.panel.set_cursor_from_name.assert_called_with('default')

    def test_resize_cursor_matches_edge_axis(self):
        for edges, expected in ((('left',),'ew-resize'), (('top',),'ns-resize'),
                                (('left','top'),'nwse-resize'), (('right','top'),'nesw-resize')):
            self.assertEqual(SpatialEditorSurface._resize_cursor(edges), expected)

    def test_busy_window_press_resumes_once_after_same_frame_is_ready(self):
        editor = self.editor()
        editor._preview = Mock(busy=True)
        gesture = Mock()
        gesture.get_current_event_time.return_value = 431
        editor._drag_begin(gesture, 60, 65)
        editor._native_drag.begin.assert_not_called()
        self.assertIsNotNone(editor._waiting_press)
        editor._preview.busy = False
        editor._resume_waiting_press()
        editor._resume_waiting_press()
        editor._native_drag.begin.assert_called_once()
        self.assertEqual(editor._native_drag.begin.call_args.args[-1], 431)

    def test_release_before_refresh_does_not_start_later(self):
        editor = self.editor()
        editor._preview = Mock(busy=True)
        gesture = Mock()
        gesture.get_current_event_time.return_value = 432
        editor._drag_begin(gesture, 60, 65)
        editor._drag_end(gesture, 0, 0)
        editor._preview.busy = False
        editor._resume_waiting_press()
        editor._native_drag.begin.assert_not_called()

    def test_waiting_press_does_not_retarget_changed_icon(self):
        editor = self.editor()
        editor._preview = Mock(busy=True)
        gesture = Mock()
        gesture.get_current_event_time.return_value = 433
        editor._drag_begin(gesture, 60, 65)
        editor._interaction_at = Mock(return_value=None)
        editor._preview.busy = False
        editor._resume_waiting_press()
        editor._native_drag.begin.assert_not_called()

    def test_pending_press_refresh_preserves_gtk_icon_children(self):
        editor = self.editor()
        editor._preview = Mock(busy=True)
        gesture = Mock()
        gesture.get_current_event_time.return_value = 434
        editor._drag_begin(gesture, 60, 65)
        editor._render_view = Mock()
        editor._accept_snapshot(replace(snapshot(), revision=4, committed_model_revision=4), None)
        editor._render_view.assert_not_called()
        self.assertIsNotNone(editor._waiting_press)
        editor._handshake.start.assert_called()

    def test_press_waits_for_new_projection_not_only_snapshot(self):
        editor = self.editor()
        editor._projection_status = 'accepted'
        gesture = Mock()
        gesture.get_current_event_time.return_value = 435
        editor._drag_begin(gesture, 60, 65)
        editor._native_drag.begin.assert_not_called()
        editor._resume_waiting_press()
        editor._native_drag.begin.assert_not_called()
        editor._projection_status = 'presented'
        editor._resume_waiting_press()
        editor._native_drag.begin.assert_called_once()

    def test_same_presented_envelope_restores_input_without_waiting_for_new_ack(self):
        editor = self.editor()
        editor._render_view = Mock()
        editor.frame = Mock(animating=False)
        editor._bounds = Mock(return_value=(0,0,400,300))
        surface = editor.window.get_surface.return_value
        editor._approved_input = (surface, (0,0,400,300))
        editor._projection_status = 'presented'
        editor._accept_snapshot(snapshot(), None)
        surface.set_input_region.assert_called_once()
        surface.queue_render.assert_called_once()
