import json
import unittest
from luminophore_shell.spatial_grab import parse_spatial_grab
from luminophore_shell.spatial_editor import SpatialEditorState, SpatialEditorMode


def payload(**changes):
    return json.dumps({"schema": 1, "source": "abc_123_456", "phase": "begin", "generation": 9007199254740993,
                       "revision": 7, "topologyRevision": 4, "output": 9007199254740995, "window": "0xAB", "floating": False, "result": None, **changes})


class SpatialGrabTests(unittest.TestCase):
    def test_event_preserves_source_and_exact_ids_and_parses_candidate(self):
        event = parse_spatial_grab(payload())
        self.assertEqual(event.generation, 9007199254740993)
        self.assertEqual(event.output_id, 9007199254740995)
        self.assertEqual(event.window, "0xab")
        self.assertEqual(event.source, "abc_123_456")
        candidate = {"schema": 1, "status": "applied", "revision": 8, "topologyRevision": 4, "windows": [], "outputViews": []}
        event = parse_spatial_grab(payload(phase="update", result=candidate))
        self.assertTrue(event.result.accepted)
        self.assertEqual(event.result.revision, 8)

    def test_invalid_event_cannot_enter_lifecycle(self):
        for change in ({"schema": True}, {"phase": []}, {"source": ""}, {"source": True}, {"generation": True},
                       {"generation": 0}, {"generation": 2**64}, {"output": 0}, {"floating": 1},
                       {"window": "0x0"}, {"window": "0x1;bad"}, {"result": {}}, {"revision": -1}):
            with self.subTest(change=change):
                self.assertIsNone(parse_spatial_grab(payload(**change)))
        self.assertIsNone(parse_spatial_grab("not-json"))
        self.assertIsNone(parse_spatial_grab("[]"))

    def test_restart_resets_counter_without_accepting_retired_source(self):
        state = SpatialEditorState()
        state.toggle()
        self.assertTrue(state.begin_transient(50, "old"))
        self.assertTrue(state.begin_transient(1, "new"))
        self.assertFalse(state.finish_transient(1, "old"))
        self.assertFalse(state.finish_transient(1))
        self.assertFalse(state.begin_transient(51, "old"))
        self.assertFalse(state.begin_transient(2))
        self.assertEqual(state.grab_source, "new")
        self.assertTrue(state.finish_transient(1, "new"))
        self.assertEqual(state.mode, SpatialEditorMode.PERSISTENT)
        self.assertFalse(state.begin_transient(1, "new"))

    def test_explicit_close_does_not_let_late_old_source_reopen(self):
        state = SpatialEditorState()
        state.begin_transient(10, "old")
        state.begin_transient(1, "new")
        state.close()
        self.assertFalse(state.finish_transient(1, "new"))
        self.assertFalse(state.begin_transient(11, "old"))
        self.assertFalse(state.visible)
        self.assertTrue(state.begin_transient(2, "new"))

    def test_pointer_update_is_finite_editor_local_geometry(self):
        self.assertEqual(parse_spatial_grab(payload(phase="update", pointer=[12.5, -3])).pointer, (12.5, -3))
        for point in ([True, 0], [float("nan"), 0], [0], "0,0"):
            self.assertIsNone(parse_spatial_grab(payload(pointer=point)))

class SettledGrabTests(unittest.TestCase):
    def test_terminal_keeps_original_revision_and_reports_final_revision(self):
        event = parse_spatial_grab(payload(phase='end', settledRevision=9, settledTopologyRevision=4, settledCommitted=True))
        self.assertEqual(event.revision, 7)
        self.assertEqual(event.settled_revision, 9)
        self.assertTrue(event.settled_committed)
        self.assertIsNone(parse_spatial_grab(payload()).settled_revision)

    def test_partial_or_nonterminal_settled_state_is_rejected(self):
        for changes in ({'settledRevision': 9}, {'settledRevision': True, 'settledTopologyRevision': 4, 'settledCommitted': True}):
            self.assertIsNone(parse_spatial_grab(payload(phase='end', **changes)))
        self.assertIsNone(parse_spatial_grab(payload(settledRevision=9, settledTopologyRevision=4, settledCommitted=True)))
