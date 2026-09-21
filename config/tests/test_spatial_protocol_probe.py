import json
import unittest
from unittest.mock import patch
from luminophore_shell.hyprland import HyprlandClient, HyprlandError


class SpatialProtocolProbeTests(unittest.TestCase):
    def test_legacy_capability_is_probed_once(self):
        client=HyprlandClient()
        payload=json.dumps({'active':True,'revision':7,'extent':{'columns':4,'rows':2},'view':{'x':0,'y':0,'columns':2,'rows':2},'windows':[]})
        with patch.object(client,'_run',side_effect=['unknown request\n',payload,payload]) as run:
            self.assertEqual(client.spatial_state().protocol_version,1)
            self.assertEqual(client.spatial_state().revision,7)
        self.assertEqual([c.args for c in run.call_args_list],[('-j','luminophorespatialstate2'),('-j','luminophorespatialstate'),('-j','luminophorespatialstate')])

    def test_corrupt_response_does_not_fall_back(self):
        client=HyprlandClient()
        with patch.object(client,'_run',return_value='broken') as run:
            with self.assertRaises(HyprlandError):client.spatial_state()
        self.assertEqual(run.call_count,1)
