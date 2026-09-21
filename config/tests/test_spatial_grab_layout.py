import tomllib
from dataclasses import replace
import subprocess
import unittest
from luminophore_shell.hyprland import HyprlandClient, HyprlandError
from luminophore_shell.spatial_edit import SpatialGrabLayout


class SpatialGrabLayoutTests(unittest.TestCase):
    def layout(self):
        return SpatialGrabLayout(9007199254740993, 7, 4, "frame-a", 5, ((2, 1, 10.25, 20.5, 40.0, 35.0),))

    def test_exact_ids_and_fractional_geometry_reach_single_typed_call(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "true\n", "")
        client = HyprlandClient(runner, hyprctl="test-hyprctl")
        self.assertTrue(client.spatial_grab_layout(self.layout()))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["test-hyprctl", "luminophorecommand"])
        data = tomllib.loads(calls[0][2])
        self.assertEqual(data['generation'], '9007199254740993')
        self.assertEqual((data['revision'], data['topology']), ('7','4'))
        self.assertEqual(data['cells'], [{'column':2,'row':1,'x':10.25,'y':20.5,'width':40.0,'height':35.0,'output':'0'}])

    def test_invalid_fields_never_reach_ipc(self):
        def runner(*args, **kwargs):
            self.fail("invalid request reached IPC")
        client = HyprlandClient(runner, hyprctl="test-hyprctl")
        for change in ({"generation": True}, {"generation": 0}, {"revision": 2**64}, {"frame_revision": -1},
                       {"frame_generation": 'frame"; bad'}, {"cells": ((True, 1, 0, 0, 1, 1),)},
                       {"cells": ((0, 0, float("nan"), 0, 1, 1),)}, {"cells": ((0, 0, "1", 0, 1, 1),)},
                       {"cells": self.layout().cells * 76}):
            with self.subTest(change=change), self.assertRaises(HyprlandError):
                client.spatial_grab_layout(replace(self.layout(), **change))

    def test_rejection_and_ambiguous_ack_do_not_retry(self):
        for ack in ("false", "ok", "nil", "error: unavailable"):
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, ack, "")
            client = HyprlandClient(runner, hyprctl="test-hyprctl")
            if ack == "false":
                self.assertFalse(client.spatial_grab_layout(self.layout()))
            else:
                with self.assertRaises(HyprlandError):
                    client.spatial_grab_layout(self.layout())
            self.assertEqual(len(calls), 1)

    def test_empty_layout_is_explicit_invalidation_request(self):
        self.assertEqual(replace(self.layout(), cells=()).payload()["cells"], [])
