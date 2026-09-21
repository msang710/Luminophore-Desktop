import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from luminophore_shell.spatial_trace import Sanitizer, snapshot


class SpatialTraceTests(unittest.TestCase):
    def test_private_fields_never_survive_nested_payloads(self):
        clean = Sanitizer().clean({'title': 'PRIVATE', 'class': 'PRIVATE', 'source': 'PRIVATE',
                                  'reason': 'PRIVATE', 'windows': [{'address': 'PRIVATE', 'title': 'PRIVATE', 'x': 1, 'y': -2}],
                                  'result': {'title': 'PRIVATE', 'windows': [{'address': 'PRIVATE', 'class': 'PRIVATE'}]}})
        self.assertNotIn('PRIVATE', json.dumps(clean))
        self.assertEqual(clean['windows'][0], {'address': 'w1', 'x': 1, 'y': -2})
        self.assertEqual(clean['result']['windows'][0]['address'], 'w1')

    def test_drag_and_committed_window_share_identity(self):
        clean = Sanitizer()
        self.assertEqual(clean.clean({'window': '0xAB'})['window'],
                         clean.clean({'windows': [{'address': '0xab'}]})['windows'][0]['address'])
        self.assertEqual(clean.clean({'window': '0xac'})['window'], 'w2')

    def test_coordinates_versions_and_cancel_diagnostics_survive(self):
        data = {'generation': 2**54, 'expectedRevision': 8, 'actualRevision': 9,
                'shellLost': False, 'targetLost': False, 'phase': 'cancel',
                'pointer': [-10.5, 300], 'fromPoint': [-1, 0], 'toPoint': [1, 0]}
        self.assertEqual(Sanitizer().clean(data), data)

    def test_invalid_values_are_not_serialized(self):
        self.assertEqual(Sanitizer().clean({'x': 'PRIVATE', 'phase': 'PRIVATE', 'pointer': [float('nan'), 0]}), {})

    def test_snapshot_reads_only_spatial_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(directory / '.socket.sock'))
                server.listen(1)
                requests = []
                def reply():
                    with server.accept()[0] as client:
                        requests.append(client.recv(1024))
                        client.sendall(b'{"revision":4,"windows":[]}')
                thread = threading.Thread(target=reply)
                thread.start()
                self.assertEqual(snapshot(directory), {'revision': 4, 'windows': []})
                thread.join(timeout=1)
                self.assertEqual(requests, [b'j/luminophorespatialstate2'])
