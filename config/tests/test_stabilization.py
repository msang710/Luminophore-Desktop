from dataclasses import replace
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import json

from luminophore_shell.window_snapshot import WindowSnapshotReader
from luminophore_shell.ui_dispatch import UiDispatchRequest
from luminophore_shell.hyprland import HyprlandClient, MonitorRecord, WorkspaceRef


class DispatchTests(TestCase):
    def test_expired_queued_request_never_executes(self):
        mutation = Mock()
        request = UiDispatchRequest(mutation)
        self.assertEqual(request.wait(0)['category'], 'cancelled')
        request.invoke()
        mutation.assert_not_called()

    def test_running_request_is_unknown_then_delivers_actual_completion(self):
        entered, release = Event(), Event()
        def mutation():
            entered.set()
            release.wait(2)
            return {'ok': True}
        request = UiDispatchRequest(mutation)
        worker = Thread(target=request.invoke)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(request.wait(0)['category'], 'completion_unknown')
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(request.wait(0), {'ok': True})

    def test_completed_result_and_exception_win_over_timeout(self):
        for dispatch, expected in [(lambda: {'ok': True}, {'ok': True}),
                                   (Mock(side_effect=ValueError('bad')), {'ok': False, 'error': 'bad'})]:
            request = UiDispatchRequest(dispatch)
            request.invoke()
            self.assertEqual(request.wait(0), expected)
            request.invoke()
            self.assertEqual(request.wait(0), expected)


class WindowReaderTests(TestCase):
    def test_slow_read_coalesces_and_main_queue_keeps_processing(self):
        entered, release = Event(), Event()
        queue, applied = Queue(), []
        calls = []
        def windows(monitors):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(2)
            return []
        reader = WindowSnapshotReader(SimpleNamespace(monitors=lambda: [], windows=windows),
                                      lambda f,*a: queue.put((f,a)), lambda *v: applied.append(v))
        try:
            reader.request()
            self.assertTrue(entered.wait(1))
            for _ in range(100): reader.request()
            # A UI task can run while the worker is blocked.
            queue.put((lambda: applied.append('input'), ()))
            f,a = queue.get(timeout=1); f(*a)
            self.assertEqual(applied, ['input'])
            release.set()
            for _ in range(2):
                f,a = queue.get(timeout=1); f(*a)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(applied), 3)
        finally:
            release.set(); reader.close(); reader._thread.join(2)

    def test_reset_and_close_discard_already_posted_results(self):
        queue, applied = Queue(), []
        reader = WindowSnapshotReader(SimpleNamespace(monitors=lambda: [], windows=lambda _: []),
                                      lambda f,*a: queue.put((f,a)), lambda *v: applied.append(v))
        try:
            reader.request()
            old = queue.get(timeout=1)
            reader.reset()
            old[0](*old[1])
            self.assertFalse(applied)
            current = queue.get(timeout=1)
            reader.close()
            current[0](*current[1])
            self.assertFalse(applied)
        finally:
            reader.close(); reader._thread.join(2)

    def test_topology_mismatch_retries_without_publishing_mixed_snapshot(self):
        m = MonitorRecord(1,'test',0,0,800,600,WorkspaceRef(1,'base'))
        moved = replace(m, x=800)
        client = SimpleNamespace(monitors=Mock(side_effect=[[m],[moved],[moved],[moved]]), windows=lambda _: [])
        queue, applied = Queue(), []
        reader = WindowSnapshotReader(client,lambda f,*a: queue.put((f,a)),lambda *v: applied.append(v))
        try:
            reader.request()
            f,a = queue.get(timeout=1); f(*a)
            self.assertEqual(applied, [([moved],[])])
        finally:
            reader.close(); reader._thread.join(2)

    def test_query_failure_keeps_last_result_and_next_request_recovers(self):
        queue,applied=Queue(),[]
        client=SimpleNamespace(monitors=Mock(side_effect=[TimeoutError(),[],[]]),windows=lambda _:[])
        reader=WindowSnapshotReader(client,lambda f,*a:queue.put((f,a)),lambda *v:applied.append(v))
        try:
            reader.request()
            f,a=queue.get(timeout=1)
            with self.assertLogs('luminophore-shell',level='WARNING'): f(*a)
            self.assertFalse(applied)
            reader.request()
            f,a=queue.get(timeout=1); f(*a)
            self.assertEqual(applied,[([],[])])
        finally:
            reader.close();reader._thread.join(2)


class ProjectionRecoveryTests(TestCase):
    def client(self):
        client = HyprlandClient()
        client._pending_projections[('luminophore-shell-a','g',42)] = (0,5)
        return client

    def test_late_matching_ack_is_accepted_but_not_wrong_content(self):
        client = self.client()
        self.assertFalse(client.shell_projection_presented('luminophore-shell-a,g,42,4'))
        self.assertTrue(client.shell_projection_presented('luminophore-shell-a,g,42,5'))
        self.assertFalse(client.shell_projection_presented('luminophore-shell-a,g,42,5'))

    def test_readback_distinguishes_committed_presented_and_other_identity(self):
        client = self.client()
        for generation,revision,content,state,expected in [
            ('g',42,5,'committed','unknown'),('g',42,5,'presented','presented'),
            ('g',42,5,'prepared','failed'),('old',42,5,'presented','failed'),
            ('g',41,5,'presented','failed'),('g',42,4,'presented','failed')]:
            client._run=Mock(return_value=json.dumps({'version':1,'receipts':[
                {'generation':generation,'revision':revision,'contentRevision':content,'state':state}]}))
            self.assertEqual(client.projection_state('a','g'),expected)

    def test_unknown_recovery_is_bounded_and_never_replays_commit(self):
        from luminophore_shell.ui.projection import submit_surface_projection
        client = self.client()
        callbacks, timers, applied = [], [], []
        client.shell_projection_async = lambda *args: callbacks.append(args[-1]) or True
        def reconcile(*_):
            callbacks[0]('unknown')
            return True
        client.reconcile_projection = Mock(side_effect=reconcile)
        owner = SimpleNamespace()
        with patch('gi.repository.GLib.timeout_add', side_effect=lambda delay,fn: timers.append(fn)):
            submit_surface_projection(owner,client.shell_projection,('a',True,'g',5,{}),applied.append)
            for _ in range(12):
                if owner._projection_status == "unavailable": break
                timers.pop(0)()
        self.assertEqual(client.reconcile_projection.call_count,3)
        self.assertEqual(len(callbacks),1)
        self.assertEqual(owner._projection_status,'unavailable')
        self.assertEqual(applied,[False])

    def test_late_presentation_cancels_recovery_timer(self):
        from luminophore_shell.ui.projection import submit_surface_projection
        client = self.client()
        callbacks,timers,applied=[],[],[]
        client.shell_projection_async=lambda *a: callbacks.append(a[-1]) or True
        client.reconcile_projection=Mock()
        owner=SimpleNamespace()
        with patch('gi.repository.GLib.timeout_add',side_effect=lambda delay,fn:timers.append(fn)):
            submit_surface_projection(owner,client.shell_projection,('a',True,'g',5,{}),applied.append)
            timers.pop(0)()
            callbacks[0]('presented')
            timers.pop(0)()
        client.reconcile_projection.assert_not_called()
        self.assertEqual(applied,[True])

    def test_late_accept_does_not_stop_readback(self):
        from luminophore_shell.ui.projection import submit_surface_projection
        client = self.client()
        callbacks,timers=[],[]
        client.shell_projection_async=lambda *a: callbacks.append(a[-1]) or True
        client.reconcile_projection=Mock(return_value=True)
        owner=SimpleNamespace()
        with patch('gi.repository.GLib.timeout_add',side_effect=lambda delay,fn:timers.append(fn)):
            submit_surface_projection(owner,client.shell_projection,('a',True,'g',5,{}),Mock())
            timers.pop(0)()
            callbacks[0]('accepted')
            self.assertEqual(owner._projection_status,'unknown')
            timers.pop(0)()
        client.reconcile_projection.assert_called_once_with('a','g')

    def test_readback_restores_native_layout_with_exact_receipt(self):
        from luminophore_shell.ui.projection import submit_surface_projection
        client=self.client()
        callbacks,applied=[],[]
        client.shell_projection_async=lambda *a: callbacks.append(a[-1]) or True
        owner=SimpleNamespace(projection_presented=Mock())
        with patch('gi.repository.GLib.timeout_add'):
            submit_surface_projection(owner,client.shell_projection,('a',True,'g',5,{}),applied.append)
            callbacks[0]('presented')
        self.assertEqual(applied,[True])
        owner.projection_presented.assert_called_once_with('luminophore-shell-a,g,42,5')
        self.assertFalse(client._pending_projections)

    def test_same_generation_new_content_ignores_old_completion(self):
        from luminophore_shell.ui.projection import submit_surface_projection
        client=self.client()
        callbacks,applied=[],[]
        client.shell_projection_async=lambda *a:callbacks.append(a[-1]) or True
        owner=SimpleNamespace()
        with patch('gi.repository.GLib.timeout_add'):
            submit_surface_projection(owner,client.shell_projection,('a',True,'g',5,{}),applied.append)
            submit_surface_projection(owner,client.shell_projection,('a',True,'g',6,{}),applied.append)
            callbacks[0]('presented')
        self.assertFalse(applied)
