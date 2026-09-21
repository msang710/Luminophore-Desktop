import tomllib
import unittest
from unittest.mock import Mock
from luminophore_shell.hyprland import HyprlandClient


class NativeCommandsTests(unittest.TestCase):
    def test_grab_command_preserves_uint64_identity(self):
        client = HyprlandClient()
        client._run = Mock(return_value='true')
        self.assertTrue(client.spatial_grab_begin('0x1234', 2**64-1, 4, 7, 9))
        endpoint, wire = client._run.call_args.args
        self.assertEqual(endpoint, 'luminophorecommand')
        value = tomllib.loads(wire)
        self.assertEqual(value['action'], 'grab-begin')
        self.assertEqual(value['revision'], str(2**64-1))

    def test_window_selector_is_data(self):
        client = HyprlandClient()
        client._run = Mock(return_value='ok')
        client.minimize('0x1234')
        endpoint, wire = client._run.call_args.args
        self.assertEqual(endpoint, 'luminophorecommand')
        self.assertEqual(tomllib.loads(wire)['window'], 'address:0x1234')
