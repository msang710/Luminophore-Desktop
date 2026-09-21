"""Execute the built watchdog against isolated process fixtures, never a desktop.

Set LUMINOPHORE_TEST_WATCHDOG to a freshly built start-hyprland binary.
"""
import json
import os
from pathlib import Path
import subprocess
import socket
import threading
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('LUMINOPHORE_TEST_WATCHDOG'), 'watchdog build required')
class WatchdogIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.watchdog = Path(os.environ['LUMINOPHORE_TEST_WATCHDOG']).resolve(strict=True)
        self.log = self.root / 'calls'
        self.program = self.root / 'compositor'
        self.program.write_text('''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
log = Path(os.environ['TEST_CALLS'])
with log.open('a') as stream:
    stream.write(json.dumps(sys.argv) + '\\n')
if '--version-json' in sys.argv:
    print('{"flags": []}')
elif '--watchdog-fd' in sys.argv:
    fd = int(sys.argv[sys.argv.index('--watchdog-fd') + 1])
    marker = log.with_suffix('.started')
    if not marker.exists():
        marker.touch()
        os.write(fd, b'vax\\n')
    else:
        os.write(fd, b'vax\\nend\\n')
''')
        self.program.chmod(0o755)
        (self.root / 'Hyprland').symlink_to(self.program)
        self.env = {**os.environ, 'PATH': str(self.root), 'TEST_CALLS': str(self.log)}

    def run_watchdog(self, *args):
        return subprocess.run([self.watchdog, *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_no_path_never_executes_system_hyprland(self):
        result = self.run_watchdog()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_relative_path_is_rejected_before_execution(self):
        result = self.run_watchdog('--path', 'Hyprland')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_probe_and_crash_restart_use_same_explicit_executable(self):
        result = self.run_watchdog('--path', str(self.program))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertTrue(any('--version-json' in call for call in calls))
        self.assertTrue(all(call[0] == str(self.program) for call in calls), calls)
        launches = [call for call in calls if '--watchdog-fd' in call]
        self.assertEqual(len(launches), 2)
        self.assertNotIn('--safe-mode', launches[0])
        self.assertIn('--safe-mode', launches[1])


@unittest.skipUnless(os.environ.get('LUMINOPHORE_TEST_CONTROL'), 'control build required')
class ControlIdentityTests(unittest.TestCase):
    def test_uses_luminophore_namespace_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            threads = []
            for namespace, signature in [('luminophore', 'current_123_456'), ('hypr', 'old_123_456')]:
                path = root / namespace / signature
                path.mkdir(parents=True)
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(path / '.socket.sock'))
                server.listen()
                server.settimeout(1)
                def serve(server=server, namespace=namespace):
                    with server:
                        try:
                            client, _ = server.accept()
                        except TimeoutError:
                            return
                        with client:
                            calls.append((namespace, client.recv(1024)))
                            client.sendall(b'ok')
                thread = threading.Thread(target=serve)
                thread.start()
                threads.append(thread)
            env = {**os.environ, 'XDG_RUNTIME_DIR': directory,
                   'LUMINOPHORE_INSTANCE_SIGNATURE': 'current_123_456',
                   'HYPRLAND_INSTANCE_SIGNATURE': 'old_123_456'}
            result = subprocess.run([os.environ['LUMINOPHORE_TEST_CONTROL'], 'version'],
                                    env=env, capture_output=True, text=True, timeout=5)
            for thread in threads:
                thread.join()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(calls, [('luminophore', b'/version')])

    def test_cannot_override_current_instance_with_another_session(self):
        result = subprocess.run([os.environ['LUMINOPHORE_TEST_CONTROL'], '-i', 'other_123_456', 'version'],
            env={**os.environ, 'LUMINOPHORE_INSTANCE_SIGNATURE': 'current_123_456'},
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('instance', result.stdout.lower())


if __name__ == '__main__':
    unittest.main()
