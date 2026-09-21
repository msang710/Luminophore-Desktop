import unittest
from luminophore_shell.domain_client import DomainClient


class DomainClientTests(unittest.TestCase):
    def test_waits_for_confirmed_generation_without_replaying(self):
        class IPC:
            def __init__(self): self.requests = []
            def request(self, request):
                self.requests.append(request)
                return {'ok': True, 'category': 'completion_unknown' if request['command'] == 'settings-apply' else 'ok',
                        'found': True, 'request_id': request['request_id'], 'digest': 'new'}
        ipc = IPC()
        result = DomainClient(client=ipc).commit({'placement.rules': {'app': 'left'}}, 'old')
        self.assertEqual(result['digest'], 'new')
        self.assertEqual([r['command'] for r in ipc.requests], ['settings-apply', 'settings-status'])

    def test_rejection_never_writes_an_alternate_file(self):
        class IPC:
            def request(self, request): return {'ok': False, 'error': 'unavailable'}
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            DomainClient(client=IPC()).commit({'bundles.bundles': []}, 'old')

    def test_lost_apply_ack_queries_same_request_without_replay(self):
        from luminophore_shell.ipc import IpcError
        class IPC:
            def __init__(self): self.requests=[]
            def request(self, request):
                self.requests.append(request)
                if request['command']=='settings-apply': raise IpcError('lost response')
                return dict(ok=True, found=True, category='ok', request_id=request['request_id'], digest='new')
        ipc=IPC()
        self.assertEqual(DomainClient(client=ipc).commit({'visual.enabled':False},'old')['digest'],'new')
        self.assertEqual([r['command'] for r in ipc.requests],['settings-apply','settings-status'])
        self.assertEqual(ipc.requests[0]['request_id'],ipc.requests[1]['request_id'])

    def test_foreign_status_receipt_cannot_complete_our_request(self):
        class IPC:
            def request(self, request):
                return dict(ok=True, found=True, category='ok', request_id='another-request')
        with self.assertRaisesRegex(ValueError,'identity'):
            DomainClient(client=IPC()).commit({'visual.enabled':False},'old')

    def test_native_keypad_zoom_ids_have_explicit_dispatch_branches(self):
        from pathlib import Path
        from luminophore_shell.binding_registry import default_registry
        source=(Path(__file__).resolve().parents[2]/'compositor/src/config/luminophore/DesktopRuntime.cpp').read_text()
        for action in default_registry().actions:
            if action.action_id.startswith('cursor.zoom_'):
                self.assertIn('action == "'+action.action_id+'"',source)

    def test_session_scoped_save_is_reported_as_pending_next_start(self):
        from luminophore_shell.settings_service import SettingsServiceHost
        host=object.__new__(SettingsServiceHost);host._restart_required=True
        request=dict(request_id='session-save',expected_digest='old',changes={'native':{'startup':['steam']}})
        receipt=host._wire_result(request,'ok')
        self.assertEqual(receipt['category'],'saved_pending_next_start')
        self.assertTrue(receipt['requires_session_restart'])
        self.assertIn('다음 세션',receipt['message'])
        self.assertEqual(DomainClient(client=object()).wait(receipt),receipt)
