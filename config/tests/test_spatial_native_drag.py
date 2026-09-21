from queue import Queue, Empty
from threading import Event
from time import monotonic
from unittest import TestCase
from unittest.mock import Mock
from luminophore_shell.spatial_preview import SpatialNativeDrag
from luminophore_shell.hyprland import HyprlandClient
from spatial_test_fixtures import snapshot


class NativeDragTests(TestCase):
    def setup_controller(self):
        queue = Queue()
        client = Mock()
        client.spatial_grab_begin.return_value = True
        changed = Mock()
        ctl = SpatialNativeDrag(client, queue.put, changed)
        self.addCleanup(ctl.worker.close)
        def drain():
            deadline = monotonic()+2
            while ctl.pending:
                if monotonic() > deadline:
                    self.fail('native drag worker did not settle')
                try:
                    queue.get(timeout=.05)()
                except Empty:
                    pass
        return ctl, client, changed, drain

    def test_begin_only_sends_identity_and_native_owns_release(self):
        ctl, client, changed, drain = self.setup_controller()
        self.assertTrue(ctl.begin('0xa', snapshot(), 10, 1234))
        drain()
        client.spatial_grab_begin.assert_called_once_with('0xa', 3, 2, 10, 1234)
        self.assertFalse(ctl.begin('0xb', snapshot(), 20, 1235))
        ctl.finished()
        self.assertTrue(ctl.begin('0xb', snapshot(), 20, 1235))
        drain()
        client.spatial_commit.assert_not_called()

    def test_cancel_while_begin_inflight_cancels_only_that_press(self):
        ctl, client, changed, drain = self.setup_controller()
        entered, release = Event(), Event()
        def begin(*_):
            entered.set()
            release.wait(2)
            return True
        client.spatial_grab_begin.side_effect = begin
        ctl.begin('0xa', snapshot(), 10, 1234)
        self.assertTrue(entered.wait(1))
        ctl.cancel()
        release.set()
        drain()
        client.spatial_grab_cancel.assert_called_once_with(1234)
        changed.assert_called_once_with(False)
        self.assertIsNone(ctl.press)

    def test_uncertain_begin_gets_identity_scoped_cancellation(self):
        ctl, client, changed, drain = self.setup_controller()
        client.spatial_grab_begin.side_effect = TimeoutError()
        ctl.begin('0xa', snapshot(), 10, 1234)
        drain()
        client.spatial_grab_cancel.assert_called_once_with(1234)
        self.assertIsNone(ctl.press)

    def test_wire_preserves_large_window_ids_without_lua_numbers(self):
        client = HyprlandClient()
        client._run = Mock(return_value='true')
        self.assertTrue(client.spatial_grab_begin('0xffffffffffffffff', 3, 2, 10, 1234))
        self.assertIn('window_id = "18446744073709551615"', client._run.call_args.args[1])
        self.assertIn('press_time = "1234"', client._run.call_args.args[1])
