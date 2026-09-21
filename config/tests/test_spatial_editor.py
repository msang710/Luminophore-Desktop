from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from luminophore_shell.hyprland import SpatialState, SpatialView
from luminophore_shell.spatial_editor import SpatialEditorMode, SpatialEditorState
from luminophore_shell.spatial_feedback import SpatialFeedback
from luminophore_shell.ui.spatial_editor import SpatialEditorSurface


def snapshot():
    return SpatialState(active=True, revision=3, columns=15, rows=5, view=SpatialView(0, 0, 2, 1), windows=(),
                        committed=True, topology_revision=1, committed_model_revision=3, committed_topology_revision=1,
                        display_origin=(7, 2))


def feedback(**kwargs):
    event = SpatialFeedback("focus", True, "", "right", True, 3, 1, "DP-1", "0xa", (7, 2), (8, 2), None, None, (7, 2))
    return replace(event, **kwargs)


class SpatialEditorTests(unittest.TestCase):
    def test_reconnect_cancels_transient_and_preserves_prior_editor_mode(self):
        for persistent in (False, True):
            state = SpatialEditorState()
            if persistent:
                state.toggle()
            state.accept(snapshot())
            self.assertTrue(state.begin_transient(7, "source"))
            state.connection_reset()
            self.assertEqual(state.mode, SpatialEditorMode.PERSISTENT if persistent else SpatialEditorMode.HIDDEN)
            self.assertIsNone(state.grab_generation)
            self.assertIsNone(state.view)
            self.assertFalse(state.finish_transient(7, "source"))
            self.assertFalse(state.begin_transient(7, "source"))
            self.assertTrue(state.begin_transient(8, "source"))

    def test_toggle_and_close_own_the_independent_lifecycle(self):
        state = SpatialEditorState()
        self.assertFalse(state.visible)
        state.toggle()
        self.assertEqual(state.mode, SpatialEditorMode.PERSISTENT)
        state.accept(snapshot())
        state.highlight(feedback())
        state.toggle()
        self.assertFalse(state.visible)
        self.assertIsNone(state.feedback)
        state.close()
        self.assertFalse(state.visible)

    def test_transient_grab_restores_hidden_or_persistent_mode(self):
        for persistent in (False, True):
            state = SpatialEditorState()
            if persistent:
                state.toggle()
            self.assertTrue(state.begin_transient(1))
            self.assertEqual(state.mode, SpatialEditorMode.TRANSIENT_DRAG)
            self.assertTrue(state.finish_transient(1))
            self.assertEqual(state.mode, SpatialEditorMode.PERSISTENT if persistent else SpatialEditorMode.HIDDEN)
            self.assertFalse(state.begin_transient(1))

    def test_new_grab_invalidates_old_release_without_losing_return_mode(self):
        state = SpatialEditorState()
        state.toggle()
        self.assertTrue(state.begin_transient(1))
        self.assertTrue(state.begin_transient(2))
        self.assertFalse(state.finish_transient(1))
        self.assertEqual(state.grab_generation, 2)
        self.assertTrue(state.finish_transient(2))
        self.assertEqual(state.mode, SpatialEditorMode.PERSISTENT)

    def test_close_during_grab_prevents_late_release_from_reopening_editor(self):
        state = SpatialEditorState()
        state.toggle()
        state.begin_transient(3)
        state.close()
        self.assertFalse(state.finish_transient(3))
        self.assertFalse(state.visible)
        for generation in (True, 0, -1, 3):
            self.assertFalse(state.begin_transient(generation))
        self.assertTrue(state.begin_transient(4))
        self.assertTrue(state.finish_transient(4))
        self.assertFalse(state.visible)

    def test_invalid_revision_preserves_last_complete_view_and_reports_diagnostic(self):
        state = SpatialEditorState()
        self.assertTrue(state.accept(snapshot()))
        before = state.view
        self.assertFalse(state.accept(replace(snapshot(), committed_model_revision=2)))
        self.assertIs(state.view, before)
        self.assertTrue(state.diagnostic)
        self.assertFalse(state.accept(replace(snapshot(), diagnostics=("desktop-leak",))))
        self.assertTrue(state.accept(replace(snapshot(), revision=4, committed_model_revision=4)))
        self.assertFalse(state.diagnostic)
        self.assertEqual(state.view.revision, (1, 4))

    def test_feedback_is_suppressed_when_open_but_only_matching_revision_highlights(self):
        state = SpatialEditorState()
        state.accept(snapshot())
        self.assertFalse(state.highlight(feedback()))
        state.toggle()
        self.assertTrue(state.highlight(feedback(revision=4)))
        self.assertIsNone(state.feedback)
        event = feedback()
        self.assertTrue(state.highlight(event))
        self.assertIs(state.feedback, event)
        state.accept(replace(snapshot(), revision=5, committed_model_revision=5))
        self.assertIsNone(state.feedback)

    def test_close_cancels_projection_and_removes_input_before_unmapping(self):
        state = SpatialEditorState()
        state.toggle()
        surface = Mock()
        window = Mock()
        window.get_surface.return_value = surface
        editor = SimpleNamespace(panel=Mock(), client=Mock(), _preview=Mock(), state=state, window=window, _handshake=Mock(), _stop_grab_geometry=Mock(), _clear_badge=Mock())
        editor._native_drag = Mock()
        SpatialEditorSurface.close(editor)
        self.assertFalse(state.visible)
        editor._handshake.cancel.assert_called_once_with()
        self.assertTrue(surface.set_input_region.call_args.args[0].is_empty())
        window.set_visible.assert_called_once_with(False)

    def test_projection_uses_bottom_surface_contract_and_enables_input_after_acceptance(self):
        state = SpatialEditorState()
        state.toggle()
        window = Mock()
        window.get_width.return_value = 700
        window.get_height.return_value = 350
        editor = SimpleNamespace(_preview=Mock(), visible=True, window=window, client=Mock(), _revision=4, state=state, _invalidate_grab_layout=Mock())
        editor._projection_applied = lambda value: SpatialEditorSurface._projection_applied(editor, value)
        editor.client.shell_projection.return_value = False
        self.assertFalse(SpatialEditorSurface._submit_projection(editor))
        self.assertTrue(window.get_surface().set_input_region.call_args.args[0].is_empty())
        editor.client.shell_projection.return_value = True
        self.assertTrue(SpatialEditorSurface._submit_projection(editor))
        self.assertFalse(window.get_surface().set_input_region.call_args.args[0].is_empty())
        args = editor.client.shell_projection.call_args.args
        self.assertEqual(args[0:2], ("spatial-editor", True))
        self.assertEqual(args[3], 4)
        self.assertEqual(args[4]["panel_width"], 700.0)

    def test_open_editor_replaces_osd_with_highlight(self):
        from luminophore_shell.app import LuminophoreShellApplication
        event = feedback()
        editor = Mock()
        editor.highlight.return_value = True
        queue = Mock()
        queue.drain.return_value = (event,)
        osd = Mock()
        app = SimpleNamespace(_spatial_feedback_timer=1, _spatial_feedback=queue,
                              spatial_editor_surface=editor, osd_layers={"DP-1": osd})
        self.assertFalse(LuminophoreShellApplication._flush_spatial_feedback(app))
        editor.highlight.assert_called_once_with(event)
        osd.show_spatial.assert_not_called()

    def test_allocation_changes_restart_projection_stability_window(self):
        window = Mock()
        window.get_width.return_value = 0
        window.get_height.return_value = 0
        editor = SimpleNamespace(window=window, _allocation=(0, 0), _revision=1, state=SpatialEditorState())
        self.assertEqual(SpatialEditorSurface._frame_revision(editor), 0)
        window.get_width.return_value = 700
        window.get_height.return_value = 350
        self.assertEqual(SpatialEditorSurface._frame_revision(editor), 2)
        self.assertEqual(SpatialEditorSurface._frame_revision(editor), 2)
        window.get_height.return_value = 380
        self.assertEqual(SpatialEditorSurface._frame_revision(editor), 3)
