import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from luminophore_shell.monitor_layout import MonitorLayout, encode, decode, validate, snapped
SNAP=dict(topology='abc',supported=True,pending=False,outputs=[dict(key='a',name='DP-1',x=0,y=0,width=1920,height=1080,scale=1),dict(key='b',name='DP-2',x=1920,y=0,width=1920,height=1080,scale=1)])
OLD={'a':[0,0],'b':[1920,0]}
NEW={'a':[1920,0],'b':[0,0]}
class MonitorLayoutTests(TestCase):
    def setUp(self):
        self.client = Mock()
        self.client._run.return_value = json.dumps(SNAP)
        self.domain = Mock()
        self.domain.snapshot.return_value = ({'monitors.toml': {'positions': {'absent': [4000,0]}, 'outputs': {}}}, 'base')
        self.domain.start.return_value = {'ok':True, 'request_id':'preview', 'category':'completion_unknown'}
        self.service = MonitorLayout(client=self.client, service=self.domain)

    def test_preview_stages_whole_generation_without_separate_file(self):
        self.service.begin(SNAP, NEW)
        changes, digest = self.domain.start.call_args.args
        self.assertEqual(digest, 'base')
        self.assertEqual(changes['monitors.outputs']['DP-1']['position'], [1920,0])
        self.assertEqual(changes['monitors.outputs']['DP-2']['position'], [0,0])
        self.assertEqual(changes['monitors.positions'], {'absent':[4000,0]})
        self.domain.wait.assert_called_once_with(self.domain.start.return_value, preview=True)
        self.client._run.assert_called_once_with('luminophoremonitorlayout', 'snapshot', timeout=3)

    def test_confirm_waits_for_joint_completion(self):
        self.service.identifier = 'preview'
        self.service.confirm()
        self.assertEqual(self.domain.request.call_args_list[0].args, ('settings-confirm',))
        self.assertTrue(self.domain.request.call_args_list[0].kwargs['keep'])
        self.domain.wait.assert_called_once()

    def test_cancel_requires_proven_rollback(self):
        self.service.identifier = 'preview'
        self.domain.request.return_value = {'ok':True, 'category':'runtime_apply_failed_rolled_back'}
        self.service.cancel()
        self.assertFalse(self.domain.request.call_args_list[0].kwargs['keep'])
        self.domain.request.return_value = {'ok':True, 'category':'conflict'}
        with self.assertRaises(ValueError): self.service.cancel()

    def test_topology_change_never_starts_candidate(self):
        self.client._run.return_value = json.dumps(dict(SNAP, topology='new'))
        with self.assertRaises(ValueError): self.service.begin(SNAP, NEW)
        self.domain.start.assert_not_called()

    def test_status_reflects_timeout_and_unknown_completion(self):
        for category, awaiting, expected in [('completion_unknown',True,'preview'),('completion_unknown',False,'confirming'),('ok',False,'committed'),('saved_pending_next_start',False,'committed'),('runtime_apply_failed_rolled_back',False,'rolled_back')]:
            self.domain.request.return_value = {'ok':True, 'category':category, 'awaiting_confirmation':awaiting}
            self.assertEqual(self.service.status(), expected)

    def test_late_confirmation_rejection_is_not_saved(self):
        self.domain.request.side_effect = ValueError('stale confirmation')
        with self.assertRaises(ValueError): self.service.confirm()
        self.domain.wait.assert_not_called()

    def test_validation_snapping(self):
        validate(SNAP,NEW);validate(SNAP,{'a':[0,-1080],'b':[0,0]})
        with self.assertRaises(ValueError):validate(SNAP,{'a':[0,0],'b':[1900,0]})
        with self.assertRaises(ValueError):validate(dict(SNAP,supported=False),NEW)
        self.assertEqual(snapped(SNAP,OLD,'b',1930,5),(1920,0))
        for point in ([True,0],[1.5,0],[100001,0]):
            with self.assertRaises(ValueError):encode({'a':point})
        with self.assertRaises(ValueError):decode(encode(OLD)+b'-- changed')

class MonitorUiCompletionTests(TestCase):
    def test_success_label_waits_for_completion(self):
        from types import SimpleNamespace
        from luminophore_shell.ui.settings_monitors import MonitorSettings
        for method in (MonitorSettings.keep, MonitorSettings.cancel):
            owner=SimpleNamespace(active=True, status=Mock(), service=Mock(), worker=Mock())
            method(owner)
            self.assertTrue(owner.active)
            owner.status.set_text.assert_not_called()
            complete=owner.worker.call_args.args[1]
            complete(None)
            self.assertFalse(owner.active)
            owner.status.set_text.assert_called_once()

    def test_drag_cancel_restores_candidate_without_ipc(self):
        from types import SimpleNamespace
        from luminophore_shell.ui.settings_monitors import MonitorSettings
        owner=SimpleNamespace(drag=('a',0,0,0.1),candidate={'a':[200,200]},
                              canvas=Mock(),service=Mock())
        MonitorSettings.cancel_drag(owner)
        self.assertEqual(owner.candidate,{'a':[0,0]})
        self.assertIsNone(owner.drag)
        owner.service.begin.assert_not_called()
