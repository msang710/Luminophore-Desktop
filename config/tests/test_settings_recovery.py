"""Process/ownership recovery contracts. Native probe uses real coordinator;
GTK callbacks here are injected. Real compositor acceptance is a separate test.
"""
import json
import subprocess
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from tests.test_settings_end_to_end import SettingsEndToEndTests as Harness
from luminophore_shell.settings_generation import fixture_store, edit_candidate
from luminophore_shell.settings_service import SettingsServiceHost
from luminophore_shell.settings_lease import SettingsLease
from luminophore_shell.settings_store import ConflictError


class SettingsRecoveryTests(TestCase):
    setUpClass = classmethod(Harness.setUpClass.__func__)
    setUp = Harness.setUp
    start_components = Harness.start_components
    stop_components = Harness.stop_components
    remote = Harness.remote
    apply = Harness.apply
    verify = Harness.verify
    pump = Harness.pump
    save = Harness.save

    def stop_host(self):
        self.host.close(); self.host._thread.join(3)
        self.assertFalse(self.host._thread.is_alive())

    def restart_host(self):
        self.host = SettingsServiceHost(SimpleNamespace(_run=self.remote), self.queue.put, self.apply, self.verify)
        self.pump(lambda:self.host.snapshot()['ok'])

    def test_living_owner_prevents_takeover(self):
        self.stop_host()
        with_lease = SettingsLease(self.store.root/'shell.lock')
        try:
            before = json.loads(self.remote('luminophoresettingsservice','status'))
            self.assertEqual(json.loads(self.remote('luminophoresettingsservice','attach'))['error'],'owner active')
            after = json.loads(self.remote('luminophoresettingsservice','status'))
            self.assertEqual(before['epoch'],after['epoch'])
        finally: with_lease.close()

    def test_old_epoch_write_rejected_after_owner_replacement(self):
        self.stop_host()
        old = fixture_store(); old.bind_epoch(json.loads((old.root/'coordinator.json').read_text())['epoch'])
        base = old.current(); candidate = edit_candidate(old,base,{'layout.panel_height':64})
        self.restart_host()
        with self.assertRaises(ConflictError): old.prepare(candidate.documents,base.id)
        self.assertEqual(old.current().id,base.id)
        self.assertFalse((old.root/'pending.json').exists())

    def test_pending_request_recovered_to_base_with_same_id(self):
        self.stop_host()
        base=self.store.current(); candidate=edit_candidate(self.store,base,{'layout.panel_height':64})
        request=dict(request_id='interrupted-before-publish',expected_digest=base.id,changes={'layout.panel_height':64})
        self.store.record_request(request,candidate.id);self.store.prepare(candidate.documents,base.id)
        self.restart_host()
        result=self.host.status(request['request_id'])
        self.assertEqual(result['category'],'runtime_apply_failed_rolled_back')
        self.assertEqual(result['digest'],base.id)
        self.assertEqual(self.actual.layout.panel_height,44)
        self.assertFalse((self.store.root/'pending.json').exists())

    def test_published_request_recovers_forward_after_ack_loss(self):
        self.stop_host()
        base=self.store.current(); candidate=edit_candidate(self.store,base,{'layout.panel_height':64})
        request=dict(request_id='interrupted-after-publish',expected_digest=base.id,changes={'layout.panel_height':64})
        self.store.record_request(request,candidate.id);self.store.prepare(candidate.documents,base.id)
        self.store.publish(candidate.id,base.id)
        self.restart_host()
        self.assertEqual(self.host.status(request['request_id'])['category'],'ok')
        self.assertEqual(self.actual.layout.panel_height,64)
        self.assertEqual(self.host.apply(request)['digest'],candidate.id)

    def test_competing_native_process_cannot_bootstrap_same_store(self):
        other=subprocess.run([str(self.binary),str(self.store.root)],input='',text=True,capture_output=True,timeout=3)
        self.assertNotEqual(other.returncode,0)
        self.assertIn('settings owner still active',other.stderr)

    def test_queued_second_client_stale_base_conflicts(self):
        base=self.store.current().id
        self.save()
        self.host.apply(dict(request_id='second-client',expected_digest=base,changes={'layout.panel_height':72}))
        self.pump(lambda:self.host.status()['category']!='completion_unknown')
        self.assertEqual(self.host.status()['category'],'conflict')
        self.assertEqual(self.actual.layout.panel_height,64)

    def test_shutdown_waits_for_running_main_callback(self):
        self.stop_host()
        # Exercise the real host callback bridge without starting another session.
        host=object.__new__(SettingsServiceHost);host._closed=threading.Event()
        started=threading.Event();release=threading.Event();callbacks=[]
        def schedule(fn):
            thread=threading.Thread(target=fn);callbacks.append(thread);thread.start()
        host.schedule=schedule
        worker=threading.Thread(target=lambda:host._main(lambda _: (started.set(),release.wait(3)),None))
        worker.start()
        try:
            self.assertTrue(started.wait(2));host._closed.set();worker.join(.2)
            self.assertTrue(worker.is_alive())
        finally:
            release.set();worker.join(3)
            for thread in callbacks:thread.join(3)
        self.assertFalse(worker.is_alive())

    def test_disk_full_prepare_rolls_back(self):
        from luminophore_shell.settings_store import SettingsStore
        with patch.object(SettingsStore,'prepare',side_effect=OSError(28,'disk full')):
            self.save()
        self.assertEqual(self.host.status()['category'],'runtime_apply_failed_rolled_back')
        self.assertEqual(self.actual.layout.panel_height,44)

    def test_native_process_restart_recovers_completed(self):
        self.save(); completed=self.store.current().id
        # Shell remains alive while the native process actually terminates.
        old=self.process;old.kill();old.wait(timeout=3)
        self.process=subprocess.Popen([str(self.binary),str(self.store.root)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.pump(lambda: self.host.snapshot().get('digest')==completed and
                  self.host.status().get('message')=='recovered from completed checkpoint')
        old.stdin.close();old.stdout.close();old.stderr.close()
        self.assertEqual(self.actual.layout.panel_height,64)

    def test_fsync_failure_after_completed_rename_recovers_forward(self):
        from luminophore_shell import settings_store as module
        original=module._sync;before=self.store.current().id;failed=[]
        def sync(path):
            if path==self.store.root and self.store.current().id!=before and not failed:
                failed.append(True);raise OSError(5,'directory fsync failed after rename')
            original(path)
        with patch.object(module,'_sync',side_effect=sync):self.save()
        self.assertEqual(failed,[True])
        self.assertEqual(self.host.status()['category'],'ok')
        self.assertEqual(self.actual.layout.panel_height,64)

    def test_journal_failure_retains_new_request_id(self):
        from luminophore_shell.settings_store import SettingsStore
        self.save()
        with patch.object(SettingsStore,'record_request',side_effect=OSError(28,'journal disk full')):
            request=dict(request_id='journal-failed',expected_digest=self.store.current().id,changes={'layout.panel_height':72})
            self.host.apply(request)
            self.pump(lambda:self.host.status().get('category')!='completion_unknown')
        self.assertEqual(self.host.status()['request_id'],'journal-failed')
        self.assertEqual(self.host.status()['category'],'runtime_apply_failed_rolled_back')
        self.assertEqual(self.actual.layout.panel_height,64)

    def test_lost_live_receipt_response_does_not_repeat_apply(self):
        original=self.host.client._run;lost=[];applies=[];apply=self.host._apply
        def apply_count(config):applies.append(config.layout.panel_height);apply(config)
        self.host._apply=apply_count
        def remote(endpoint,wire):
            answer=original(endpoint,wire)
            if wire.startswith('receipt ') and ' shell apply ' in wire and not lost:
                lost.append(True);raise OSError('response lost after delivery')
            return answer
        self.host.client._run=remote
        self.save()
        self.assertEqual(lost,[True]);self.assertEqual(applies.count(64),1)
        self.assertEqual(self.host.status()['category'],'ok')

    def test_disk_worker_exit_recovers_after_quiescence(self):
        from luminophore_shell.settings_checkpoint import CheckpointWorker
        def stopped(worker):return
        with patch.object(CheckpointWorker,'_run',stopped):
            self.save()
        self.assertEqual(self.host.status()['category'],'runtime_apply_failed_rolled_back')
        self.assertEqual(self.actual.layout.panel_height,44)

    def test_failed_initialization_exposes_original_request_as_unknown(self):
        self.save();request_id=self.host.status()['request_id'];self.stop_host()
        def fail(config):raise RuntimeError('consumer initialization failed')
        self.host=SettingsServiceHost(SimpleNamespace(_run=self.remote),self.queue.put,fail,self.verify)
        self.pump(lambda:'consumer initialization failed' in self.host._error)
        result=self.host.status(request_id)
        self.assertTrue(result['found']);self.assertEqual(result['category'],'completion_unknown')
        self.assertIn('consumer initialization failed',result['recovery_error'])
        self.assertFalse(self.host.snapshot()['ok'])
        self.stop_host();self.restart_host()
        self.assertEqual(self.host.status(request_id)['category'],'ok')

    def test_native_recovery_waits_for_write_lock_without_blocking_requests(self):
        import fcntl,time
        self.stop_host()
        with (self.store.root/'write.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                before=time.monotonic()
                state=json.loads(self.remote('luminophoresettingsservice','attach'))
                self.assertTrue(state['recovering'])
                state=json.loads(self.remote('luminophoresettingsservice','status'))
                self.assertTrue(state['recovering'])
                self.assertLess(time.monotonic()-before,.5)
            finally:fcntl.flock(lock,fcntl.LOCK_UN)
        self.pump(lambda:not json.loads(self.remote('luminophoresettingsservice','status'))['recovering'])

    def test_candidate_file_fsync_failure_cleans_partial_write(self):
        import os
        from pathlib import Path
        original=os.fsync;failed=[]
        def sync(fd):
            if '.candidate-' in os.readlink(f'/proc/self/fd/{fd}'):
                failed.append(True);raise OSError(28,'candidate disk full')
            original(fd)
        with patch.object(os,'fsync',side_effect=sync):self.save()
        self.assertTrue(failed)
        self.assertEqual(self.host.status()['category'],'runtime_apply_failed_rolled_back')
        self.assertEqual(self.actual.layout.panel_height,44)
        self.assertFalse(list(self.store.generations.glob('.candidate-*')))

    def test_late_receipt_from_previous_epoch_is_rejected(self):
        self.stop_host();base=self.store.current()
        candidate=edit_candidate(self.store,base,{'layout.panel_height':64})
        self.store.prepare(candidate.documents,base.id)
        state=json.loads(self.remote('luminophoresettingsservice','status'))
        state=json.loads(self.remote('luminophoresettingsservice',f"start {state['epoch']} 1 {base.id} {candidate.id}"))
        command=state['command'];self.assertTrue(command)
        self.remote('luminophoresettingsservice','attach')
        self.pump(lambda:not json.loads(self.remote('luminophoresettingsservice','status'))['recovering'])
        rejected=json.loads(self.remote('luminophoresettingsservice','receipt '+command+' ok '+base.id))
        self.assertIn('error',rejected)
        current=json.loads(self.remote('luminophoresettingsservice','status'))
        self.assertEqual(current['state'],'idle');self.assertEqual(current['confirmed'],base.id)


# Do not rediscover the seven imported harness tests in this module.
del Harness

import os
import unittest
@unittest.skipUnless(os.environ.get('LUMINOPHORE_RUN_SETTINGS_ACCEPTANCE')=='1',
                     'explicit isolated GPU/Wayland recovery acceptance')
class SettingsRecoveryDisplayTests(TestCase):
    def test_actual_shell_kill_at_checkpoint_boundaries(self):
        import sys
        from tests.test_settings_end_to_end import ROOT
        result=subprocess.run(['dbus-run-session','--',sys.executable,'-c',
            'from tests.test_settings_end_to_end import run_isolated_acceptance; run_isolated_acceptance(recovery=True)'],
            cwd=ROOT/'config',capture_output=True,text=True,timeout=300)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS /tmp/lb1-',result.stdout)
        print(result.stdout)
