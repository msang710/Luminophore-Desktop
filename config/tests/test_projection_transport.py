import threading
import time
import unittest
from types import SimpleNamespace
from luminophore_shell.projection_transport import ProjectionTransport, ProjectionRequest
from luminophore_shell.shell_visibility import occupied_outputs


class ProjectionTransportTests(unittest.TestCase):
    def test_latest_per_surface_is_bounded_and_other_surface_is_not_starved(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls, callbacks = [], []
        def send(name, overlay, generation, revision, style):
            calls.append((name, revision))
            if len(calls) == 1:
                started.set()
                release.wait(2)
            if len(calls) == 3:
                finished.set()
            return True
        client = SimpleNamespace(_projection_local=threading.local(), shell_projection=send)
        queue = ProjectionTransport(client, lambda fn,*args: callbacks.append((fn,args)))
        try:
            queue.submit(ProjectionRequest('a', True, '1', 1, ()), lambda s: None)
            self.assertTrue(started.wait(1))
            for i in range(2, 1002):
                queue.submit(ProjectionRequest('a', True, str(i), i, ()), lambda s: None)
            queue.submit(ProjectionRequest('b', True, 'b', 1, ()), lambda s: None)
            self.assertEqual(len(queue._pending), 2)
            release.set()
            self.assertTrue(finished.wait(1))
            self.assertEqual(calls, [('a',1),('a',1001),('b',1)])
        finally:
            release.set();queue.close()

    def test_reset_discards_queued_and_late_presentations(self):
        callbacks, delivered = [], []
        client = SimpleNamespace(_projection_local=threading.local(), shell_projection=lambda *a: True)
        queue=ProjectionTransport(client,lambda f,*a: callbacks.append((f,a)))
        queue.submit(ProjectionRequest('a',True,'g',1,()),delivered.append)
        queue.presented('luminophore-shell-a','g',1)
        queue.reset()
        for fn,args in callbacks: fn(*args)
        self.assertEqual(delivered,[])
        queue.close()

    def test_visible_fragments_are_the_monitor_authority(self):
        state=SimpleNamespace(committed=True,revision=2,committed_model_revision=2,
            topology_revision=3,committed_topology_revision=3,diagnostics=(),presentation_mode='normal',
            output_names=((10,'DP-1'),(20,'DP-2')), windows=(
                SimpleNamespace(visible=False,mode='tiled',fragment_outputs=(10,)),
                SimpleNamespace(visible=True,mode='floating',fragment_outputs=(10,)),
                SimpleNamespace(visible=True,mode='tiled',fragment_outputs=(20,))))
        self.assertEqual(occupied_outputs(state),frozenset({'DP-2'}))
        state.windows=(SimpleNamespace(visible=True,mode='tiled',fragment_outputs=(10,20)),)
        state.presentation_mode='wide'
        self.assertEqual(occupied_outputs(state),frozenset({'DP-1','DP-2'}))
        state.presentation_mode='desktop'
        self.assertFalse(occupied_outputs(state))
        state.presentation_mode='normal';state.committed_model_revision=1
        self.assertFalse(occupied_outputs(state))

    def test_projection_warning_with_zero_exit_is_rejected(self):
        import os, subprocess
        from unittest.mock import patch
        from luminophore_shell.hyprland import HyprlandClient
        calls=[]
        def run(command, **kw):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, 'warning: LUMINOPHORE Shell projection transaction rejected')
        with patch.dict(os.environ, {'LUMINOPHORE_COMPOSITOR':'1'}):
            client=HyprlandClient(run,hyprctl='hyprctl')
            self.assertFalse(client.shell_projection('osd-DP-1',True,'g',1,{}))
            self.assertFalse(client._pending_projections)
            self.assertEqual(len(calls),2)  # prepare rejected, abort cleanup; never commit
            self.assertNotIn('phase="commit"',calls[1][-1])

    def test_hyprctl_removes_only_shell_preload_and_preserves_parent(self):
        import os,subprocess
        from unittest.mock import patch
        from luminophore_shell.hyprland import HyprlandClient
        seen=[]
        def run(cmd,**kw):
            seen.append(kw['env'].get('LD_PRELOAD'))
            return subprocess.CompletedProcess(cmd,0,'ok')
        preload='/other.so:/usr/lib/libgtk4-layer-shell.so'
        with patch.dict(os.environ,{'LD_PRELOAD':preload}):
            HyprlandClient(run,hyprctl='hyprctl')._run('version')
            self.assertEqual(os.environ['LD_PRELOAD'],preload)
        self.assertEqual(seen,['/other.so'])

    def test_surface_ownership_waits_for_presentation_and_ignores_old_generation(self):
        from unittest.mock import patch
        from luminophore_shell.hyprland import HyprlandClient
        from luminophore_shell.ui.projection import submit_surface_projection
        client=HyprlandClient()
        callbacks=[]
        client.shell_projection_async=lambda *args: callbacks.append(args[-1]) or True
        owner=SimpleNamespace()
        applied=[]
        with patch('gi.repository.GLib.timeout_add'):
            self.assertTrue(submit_surface_projection(owner,client.shell_projection,('a',True,'g1',1,{}),applied.append))
            callbacks[0]('accepted')
            self.assertFalse(applied)
            submit_surface_projection(owner,client.shell_projection,('a',True,'g2',2,{}),applied.append)
            callbacks[0]('presented')
            self.assertFalse(applied)
            callbacks[1]('presented')
            callbacks[1]('accepted')
            self.assertEqual(applied,[True])
            self.assertEqual(owner._projection_status,'presented')

    def test_visibility_late_read_cannot_complete_newer_request(self):
        from unittest.mock import patch
        from luminophore_shell.app import LuminophoreShellApplication
        reads, completed = [], []
        owner=SimpleNamespace(_visibility_reader=SimpleNamespace(request=reads.append))
        with patch('gi.repository.GLib.timeout_add'):
            LuminophoreShellApplication._request_overview_visibility(owner,completed.append)
            LuminophoreShellApplication._request_overview_visibility(owner,completed.append)
            reads[0](frozenset({'DP-1'}))
            self.assertFalse(completed)
            reads[1](frozenset({'DP-2'}))
            reads[1](frozenset())
            self.assertEqual(completed,[frozenset({'DP-2'})])

    def test_recovery_read_is_coalesced_and_reset_discards_its_reply(self):
        from queue import Queue
        entered, release = threading.Event(), threading.Event()
        posted, delivered = Queue(), []
        def read(*_):
            entered.set()
            release.wait(2)
            return 'presented'
        client=SimpleNamespace(_projection_local=threading.local(), shell_projection=lambda *a:True, projection_state=read)
        worker=ProjectionTransport(client,lambda f,*a:posted.put((f,a)))
        try:
            worker.submit(ProjectionRequest('a',True,'g',1,()),delivered.append)
            f,a=posted.get(timeout=1); f(*a)
            self.assertTrue(worker.reconcile('a','g'))
            self.assertTrue(entered.wait(1))
            for _ in range(100): self.assertTrue(worker.reconcile('a','g'))
            self.assertFalse(worker._checks)
            worker.reset()
            release.set()
            f,a=posted.get(timeout=1); f(*a)
            self.assertEqual(delivered,['accepted'])
        finally:
            release.set();worker.close();worker._thread.join(2)
