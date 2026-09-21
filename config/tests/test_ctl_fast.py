from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class LightweightCtlTests(unittest.TestCase):
    def test_entry_ctl_does_not_import_graphics_config_or_bootstrap(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
import importlib.abc, json, runpy, sys
class RejectHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname in {
            'luminophore_shell.config', 'luminophore_shell.bootstrap',
            'luminophore_shell.state', 'luminophore_shell.app', 'luminophore_shell.ipc'}:
            raise AssertionError('heavy ctl import: ' + fullname)
sys.meta_path.insert(0, RejectHeavy())
import socket
class Connection:
    def settimeout(self, value): assert value == 2.0
    def connect(self, path): assert path.endswith('/luminophore-shell/control.sock')
    def sendall(self, payload): assert json.loads(payload) == {'command':'status'}
    def recv(self, size): return b'{"ok":true,"pid":123}\n'
    def close(self): pass
socket.socket = lambda *args: Connection()
sys.argv = [sys.argv[1], 'ctl', 'status']
runpy.run_path(sys.argv[0], run_name='__main__')
'''
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        env = dict(os.environ, XDG_RUNTIME_DIR=runtime.name, PYTHONDONTWRITEBYTECODE='1')
        # A ctl request does not need to initialize private graphics resources.
        env['LUMINOPHORE_RELEASE_ROOT'] = '/missing-graphics-is-not-used-by-ctl'
        for entry in (root / 'luminophore-shell', root / 'luminophore_shell/__main__.py'):
            result = subprocess.run([sys.executable, '-B', '-c', script, str(entry)],
                cwd=root, env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'ok': True, 'pid': 123})

    def test_all_existing_ctl_commands_keep_payload_contract(self):
        from luminophore_shell.ctl import parser, request_payload
        cases = [
            (['status'], {'command':'status'}),
            (['reload'], {'command':'reload'}),
            (['refresh','weather'], {'command':'refresh','provider':'weather'}),
            (['wallpaper','apply','my scene'], {'command':'wallpaper','action':'apply','scene':'my scene'}),
            (['wallpaper','status'], {'command':'wallpaper','action':'status','scene':''}),
        ]
        for action in ('open','list','current','status','apply','next','previous'):
            cases.append((['wallpaper',action], {'command':'wallpaper','action':action,'scene':''}))
        for panel in ('spatial-editor','overview','launcher','notifications','power','weather','system','spotify'):
            cases.append((['toggle', panel], {'command':'toggle','panel':panel}))
        for panel in ('launcher','notifications','power','weather','system','spotify'):
            cases.append((['open', panel], {'command':'open','panel':panel,'provider':'default'}))
        for provider in ('default','clip','file','web','command','emoji','wallpaper','palette','settings','system-theme','controls'):
            cases.append((['open','launcher','--provider',provider], {'command':'open','panel':'launcher','provider':provider}))
        for action in ('volume-up','volume-down','volume-mute','mic-mute','media-toggle','media-next','media-previous',
                       'brightness-up','brightness-down','brightness-preview-up','brightness-preview-down','brightness-commit'):
            cases.append((['hardware',action], {'command':'hardware','action':action}))
        for action in ('start','reset','stop'):
            cases.append((['glow-probe',action], {'command':'glow-probe','action':action}))
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(request_payload(parser().parse_args(['ctl', *argv])), expected)

    def test_output_status_error_and_transport_exit_codes(self):
        from luminophore_shell.ctl import main
        from luminophore_shell.ipc_client import IpcError
        cases = [({'ok':True}, 0), ({'ok':False,'error':'rejected'}, 1), (IpcError('daemon connection is unavailable'), 1)]
        for reply, code in cases:
            with self.subTest(reply=reply), patch('luminophore_shell.ctl.IpcClient') as client:
                if isinstance(reply, Exception): client.return_value.request.side_effect = reply
                else: client.return_value.request.return_value = reply
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(main(['ctl','status']), code)
                if isinstance(reply, Exception):
                    self.assertEqual(err.getvalue(), 'luminophore-shell: daemon connection is unavailable\n')
                else:
                    self.assertEqual(out.getvalue(), json.dumps(reply, ensure_ascii=False, indent=2) + '\n')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            main(['ctl','hardware','invalid-action'])
        self.assertEqual(failure.exception.code, 2)

    def test_transport_reexport_keeps_same_error_identity(self):
        from luminophore_shell import ipc, ipc_client
        self.assertIs(ipc.IpcClient, ipc_client.IpcClient)
        self.assertIs(ipc.IpcError, ipc_client.IpcError)


if __name__ == '__main__':
    unittest.main()
