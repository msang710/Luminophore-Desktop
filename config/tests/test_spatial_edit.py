from dataclasses import replace
import json
import subprocess
import unittest

from luminophore_shell.hyprland import HyprlandClient, HyprlandError
from luminophore_shell.spatial_edit import SpatialEditRequest, parse_edit_preview


class SpatialEditTests(unittest.TestCase):
    def request(self):
        return SpatialEditRequest("move-window", 17, 9, 9007199254740993, 7, 3, "0xabc")

    def test_preview_preserves_exact_revisions_and_output_in_argv(self):
        calls = []
        response = {"schema": 1, "status": "applied", "revision": 18, "topologyRevision": 9,
                    "windows": [{"address": "0xabc", "x": 7, "y": 3, "mode": "tiled"}],
                    "outputViews": [{"output": 9007199254740993, "x": 0, "y": 0, "columns": 2, "rows": 2}]}
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(response), "")
        client = HyprlandClient(runner, hyprctl="test-hyprctl")
        result = client.spatial_preview(self.request())
        self.assertTrue(result.accepted)
        self.assertEqual(result.revision, 18)
        self.assertEqual(result.windows, (("0xabc", 7, 3, "tiled"),))
        self.assertEqual(calls, [["test-hyprctl", "-j", "luminophorespatialpreview", "move-window", "17", "9", "9007199254740993", "0xabc", "7", "3"]])

    def test_requests_for_move_and_resize_are_explicit(self):
        request = replace(self.request(), action="move-view", window="")
        self.assertEqual(request.arguments()[-2:], ("7", "3"))
        request = replace(request, action="resize-view", columns=2, rows=1)
        self.assertEqual(request.arguments()[-4:], ("7", "3", "2", "1"))

    def test_invalid_requests_never_invoke_the_runner(self):
        def runner(*args, **kwargs):
            self.fail("invalid edit reached IPC")
        client = HyprlandClient(runner, hyprctl="test-hyprctl")
        for request in (replace(self.request(), revision=-1), replace(self.request(), output_id=0),
                        replace(self.request(), window="0xabc; exec bad"), replace(self.request(), x=True)):
            with self.assertRaises(HyprlandError):
                client.spatial_preview(request)

    def test_rejected_preview_is_not_success_and_malformed_response_is_rejected(self):
        data = {"schema": 1, "status": "stale-revision", "revision": 20, "topologyRevision": 9, "windows": [], "outputViews": []}
        self.assertFalse(parse_edit_preview(data).accepted)
        for update in ({"schema": 2}, {"status": "ok"}, {"revision": "20"}, {"windows": [{}]}):
            with self.assertRaises((ValueError, TypeError)):
                parse_edit_preview({**data, **update})

    def test_commit_calls_typed_dispatcher_once_and_returns_rejection(self):
        calls = []
        data = {"schema": 1, "status": "stale-revision", "revision": 20, "topologyRevision": 9, "windows": [], "outputViews": []}
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(data), "")
        result = HyprlandClient(runner, hyprctl="test-hyprctl").spatial_commit(self.request())
        self.assertFalse(result.accepted)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["test-hyprctl", "-j", "luminophorespatialcommit", "move-window", "17", "9", "9007199254740993", "0xabc", "7", "3"])

    def test_commit_rejects_invalid_input_and_ambiguous_ack_without_retry(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "ok", "")
        client = HyprlandClient(runner, hyprctl="test-hyprctl")
        with self.assertRaises(HyprlandError):
            client.spatial_commit(replace(self.request(), window='0xabc"; bad'))
        self.assertEqual(calls, [])
        with self.assertRaises(HyprlandError):
            client.spatial_commit(self.request())
        self.assertEqual(len(calls), 1)

    def test_view_commands_keep_explicit_geometry(self):
        request = replace(self.request(), action="move-view")
        self.assertEqual(request.arguments()[0], 'move-view')
        self.assertNotIn(request.window, request.arguments())
        request = replace(request, action="resize-view", columns=2, rows=1)
        self.assertEqual(request.arguments()[0], 'resize-view')
        self.assertEqual(request.arguments()[-2:], ('2', '1'))
