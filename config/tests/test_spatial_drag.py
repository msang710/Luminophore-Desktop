from dataclasses import replace
from unittest.mock import Mock
import unittest
from luminophore_shell.hyprland import HyprlandError, SpatialOutputView, SpatialState, SpatialView, SpatialWindow
from luminophore_shell.spatial_edit import SpatialEditPreview
from luminophore_shell.spatial_editor import SpatialEditorState, SpatialEditSession


from spatial_test_fixtures import snapshot, result
from test_spatial_preview import PreviewHarness


class SpatialDragTests(unittest.TestCase):
    def setup_drag(self, action="move-window", **kwargs):
        session, client = SpatialEditSession(), Mock()
        client.spatial_preview.return_value = result()
        client.spatial_commit.return_value = result()
        self.assertTrue(session.begin(snapshot(), action, 20, (1, 1), window="0xb", **kwargs))
        harness = PreviewHarness(session, client)
        self.addCleanup(harness.close)
        session._test_harness = harness
        return session, client

    def preview(self, session, client, point):
        harness = session._test_harness
        harness.controller.preview(point)
        harness.drain()
        return session.preview

    def release(self, session, client, point):
        harness = session._test_harness
        before = client.spatial_commit.call_count
        harness.controller.release(point)
        harness.drain()
        return client.spatial_commit.return_value if client.spatial_commit.call_count > before else None

    def test_preview_reuse_and_duplicate_release(self):
        session, client = self.setup_drag()
        self.preview(session, client, (4, 2))
        self.preview(session, client, (4, 2))
        self.assertEqual(client.spatial_preview.call_count, 1)
        request = client.spatial_preview.call_args.args[0]
        self.assertEqual((request.x, request.y, request.revision, request.topology_revision), (4, 2, 3, 2))
        self.assertTrue(self.release(session, client, (4, 2)).accepted)
        self.assertIsNone(self.release(session, client, (4, 2)))
        client.spatial_commit.assert_called_once_with(request)
        self.assertTrue(session.refresh_required)

    def test_final_position_has_own_preview_and_view_keeps_grab_offset(self):
        session, client = self.setup_drag("move-view")
        self.preview(session, client, (2, 1))
        self.release(session, client, (3, 2))
        self.assertEqual(client.spatial_preview.call_count, 2)
        request = client.spatial_commit.call_args.args[0]
        self.assertEqual((request.output_id, request.x, request.y), (20, 5, 1))

    def test_resize_preserves_opposite_edges_without_clamping(self):
        session, _ = self.setup_drag("resize-view", edges=("left", "top"))
        request = session.drag.request_at((2, 2))
        self.assertEqual((request.x, request.y, request.columns, request.rows), (4, 1, 2, 1))
        request = session.drag.request_at((9, 9))
        self.assertLess(request.columns, 0)
        self.assertLess(request.rows, 0)
        session, _ = self.setup_drag("resize-view", edges=("right", "bottom"))
        request = session.drag.request_at((2, 2))
        self.assertEqual((request.x, request.y, request.columns, request.rows), (3, 0, 4, 3))

    def test_cancel_and_rejected_final_preview_never_commit(self):
        session, client = self.setup_drag()
        self.preview(session, client, (2, 1))
        session.cancel()
        self.release(session, client, (2, 1))
        session.begin(snapshot(), "move-window", 10, (1, 1), window="0xb")
        self.preview(session, client, (2, 1))
        client.spatial_preview.return_value = result("no-capacity", 3)
        self.release(session, client, (99, 1))
        client.spatial_commit.assert_not_called()

    def test_invalid_begin_targets_and_geometry_modes(self):
        session = SpatialEditSession()
        self.assertFalse(session.begin(snapshot(), "move-window", 10, (0, 0), window="0xmissing"))
        self.assertFalse(session.begin(snapshot(), "move-view", 99, (0, 0)))
        self.assertFalse(session.begin(snapshot(), "resize-view", 10, (0, 0), edges=("left", "right")))
        self.assertFalse(session.begin(replace(snapshot(), committed=False), "move-view", 10, (0, 0)))
        self.assertFalse(session.begin(replace(snapshot(), presentation_mode="wide"), "move-view", 10, (0, 0)))

    def test_stale_preview_cancels_without_automatic_replay(self):
        for status in ("stale-revision", "stale-topology"):
            session, client = self.setup_drag()
            client.spatial_preview.return_value = result(status, 5, 3)
            self.release(session, client, (2, 1))
            self.assertFalse(session.active)
            self.assertTrue(session.refresh_required)
            client.spatial_commit.assert_not_called()

    def test_snapshot_change_and_close_cancel_gesture(self):
        state = SpatialEditorState()
        state.toggle()
        state.accept(snapshot())
        state.edits.begin(snapshot(), "move-view", 10, (0, 0))
        state.accept(replace(snapshot(), revision=4, committed_model_revision=4))
        self.assertFalse(state.edits.active)
        state.edits.begin(state.view.state, "move-view", 10, (0, 0))
        state.close()
        self.assertFalse(state.edits.active)

    def test_preview_failure_and_uncertain_commit_cannot_replay(self):
        session, client = self.setup_drag()
        self.preview(session, client, (2, 1))
        client.spatial_preview.side_effect = HyprlandError("timeout")
        self.release(session, client, (3, 1))
        self.release(session, client, (3, 1))
        client.spatial_commit.assert_not_called()
        client.spatial_preview.side_effect = None
        session.begin(snapshot(), "move-view", 10, (0, 0))
        client.spatial_commit.side_effect = HyprlandError("timeout after possible commit")
        self.release(session, client, (1, 0))
        self.release(session, client, (1, 0))
        self.assertEqual(client.spatial_commit.call_count, 1)
        self.assertTrue(session.refresh_required)

    def test_success_with_wrong_preview_revision_cannot_commit(self):
        for preview in (result(revision=99), result(topology=99), result("no-change", 4)):
            session, client = self.setup_drag()
            client.spatial_preview.return_value = preview
            self.release(session, client, (2, 1))
            client.spatial_commit.assert_not_called()
            self.assertTrue(session.refresh_required)
