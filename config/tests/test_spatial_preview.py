from dataclasses import replace
from queue import Queue, Empty
from threading import Event, get_ident
from time import monotonic
from unittest.mock import Mock
import unittest

from luminophore_shell.spatial_editor import SpatialEditSession
from luminophore_shell.spatial_preview import SpatialPreviewController
from spatial_test_fixtures import snapshot, result


class PreviewHarness:
    def __init__(self, session=None, client=None, changed=None, finished=None):
        self.session = session or SpatialEditSession()
        self.client = client or Mock()
        if client is None:
            self.client.spatial_preview.return_value = result()
            self.client.spatial_commit.return_value = result()
            self.client.spatial_state.return_value = snapshot()
        self.queue = Queue()
        self.changed = changed or Mock()
        self.finished = finished or Mock()
        self.controller = SpatialPreviewController(self.session, self.client, self.queue.put, self.changed, self.finished)

    def begin(self):
        return self.session.begin(snapshot(), "move-window", 10, (1, 1), window="0xa")

    def step(self):
        self.queue.get(timeout=3)()

    def drain(self):
        deadline = monotonic() + 5
        while self.controller._inflight is not None or not self.queue.empty():
            if monotonic() > deadline:
                raise AssertionError("editor worker did not settle")
            self.step()

    def close(self):
        self.controller.close()
        self.controller.worker._thread.join(3)
        if self.controller.worker._thread.is_alive():
            raise AssertionError("editor worker did not stop")


class SpatialPreviewTests(unittest.TestCase):
    def harness(self):
        harness = PreviewHarness()
        self.addCleanup(harness.close)
        self.assertTrue(harness.begin())
        return harness

    def test_thousand_identical_samples_one_preview_and_single_commit(self):
        h = self.harness()
        for _ in range(1000):
            h.controller.preview((3, 2))
        h.drain()
        for _ in range(1000):
            h.controller.preview((3, 2))
        self.assertEqual(h.client.spatial_preview.call_count, 1)
        h.controller.release((3, 2))
        h.controller.release((5, 2))
        h.drain()
        h.client.spatial_commit.assert_called_once()
        self.assertEqual(h.client.spatial_commit.call_args.args[0].x, 3)
        self.assertFalse(h.controller.busy)

    def test_slow_transport_keeps_input_free_and_only_latest_candidate_waits(self):
        h = self.harness()
        started, release = Event(), Event()
        def slow(request):
            started.set()
            self.assertTrue(release.wait(3))
            return result()
        h.client.spatial_preview.side_effect = slow
        h.controller.preview((2, 1))
        self.assertTrue(started.wait(3))
        for i in range(1000):
            h.controller.preview((i % 15, 2))
        h.controller.release((4, 2))
        self.assertEqual(h.client.spatial_preview.call_count, 1)
        self.assertIsNone(h.controller.worker._pending)
        release.set()
        h.drain()
        self.assertEqual(h.client.spatial_preview.call_count, 2)
        self.assertEqual(h.client.spatial_commit.call_args.args[0].x, 4)
        self.assertEqual(h.client.spatial_commit.call_count, 1)

    def test_main_loop_delivers_callbacks_on_owner_thread(self):
        from gi.repository import GLib
        h = self.harness()
        started, release, tick = Event(), Event(), Event()
        owner = get_ident()
        calls = []
        h.controller.worker._deliver = GLib.idle_add
        h.controller._changed = lambda _value: calls.append(get_ident())
        def slow(_request):
            started.set()
            release.wait(3)
            return result()
        h.client.spatial_preview.side_effect = slow
        h.controller.preview((3, 2))
        self.assertTrue(started.wait(3))
        GLib.idle_add(lambda: (tick.set(), False)[1])
        context = GLib.MainContext.default()
        while not tick.is_set():
            context.iteration(True)
        release.set()
        while h.controller._inflight is not None:
            context.iteration(True)
        self.assertTrue(calls)
        self.assertEqual(set(calls), {owner})

    def test_cancel_close_reconnect_or_allocation_before_response_never_commits(self):
        for action in ("cancel", "close", "session-reset", "topology"):
            with self.subTest(action=action):
                h = self.harness()
                h.controller.release((3, 2))
                # The result is queued, but has not been accepted by the UI.
                callback = h.queue.get(timeout=3)
                if action == "cancel":
                    h.controller.cancel()
                elif action == "close":
                    h.controller.close()
                elif action == "session-reset":
                    h.session.cancel("allocation/reconnect")
                else:
                    h.session.observe(replace(snapshot(), topology_revision=5, committed_topology_revision=5))
                callback()
                h.drain()
                h.client.spatial_commit.assert_not_called()

    def test_cancel_after_dispatch_is_unknown_and_never_replays(self):
        h = self.harness()
        started, release = Event(), Event()
        def commit(_request):
            started.set()
            release.wait(3)
            return result()
        h.client.spatial_commit.side_effect = commit
        h.controller.release((3, 2))
        h.step()
        self.assertTrue(started.wait(3))
        h.controller.cancel("취소")
        self.assertIn("다시 확인", h.session.message)
        release.set()
        h.drain()
        h.controller.release((3, 2))
        h.client.spatial_commit.assert_called_once()

    def test_new_gesture_cannot_accept_an_old_result(self):
        h = self.harness()
        h.controller.preview((2, 1))
        callback = h.queue.get(timeout=3)
        h.controller.cancel()
        h.begin()
        h.controller.release((4, 2))
        callback()
        h.drain()
        h.client.spatial_commit.assert_called_once()
        self.assertEqual(h.client.spatial_commit.call_args.args[0].x, 4)

    def test_outside_then_back_restores_cached_preview(self):
        h = self.harness()
        h.controller.preview((3, 2))
        h.drain()
        h.controller.preview(None)
        self.assertIsNone(h.session.preview)
        h.controller.preview((3, 2))
        h.drain()
        self.assertIsNotNone(h.session.preview)
        self.assertEqual(h.client.spatial_preview.call_count, 1)

    def test_stale_malformed_invalid_and_timeout_do_not_commit(self):
        for response in (result("stale-topology", 4, 3), result("no-capacity", 3), result("applied", 99), TimeoutError()):
            with self.subTest(response=response):
                h = self.harness()
                if isinstance(response, Exception):
                    h.client.spatial_preview.side_effect = response
                else:
                    h.client.spatial_preview.return_value = response
                h.controller.release((3, 2))
                h.drain()
                h.client.spatial_commit.assert_not_called()
                self.assertFalse(h.session.active)

    def test_refresh_is_async_and_can_invalidate_final_candidate(self):
        h = self.harness()
        h.controller.preview((3, 2))
        h.controller.refresh(lambda value, error: h.session.observe(value))
        h.client.spatial_state.return_value = replace(snapshot(), revision=9, committed_model_revision=9)
        h.controller.release((3, 2))
        h.drain()
        h.client.spatial_state.assert_called_once()
        h.client.spatial_commit.assert_not_called()

    def test_commit_timeout_refreshes_without_replay(self):
        h = self.harness()
        h.client.spatial_commit.side_effect = TimeoutError()
        h.controller.release((3, 2))
        h.drain()
        self.assertTrue(h.session.refresh_required)
        h.controller.release((3, 2))
        h.client.spatial_commit.assert_called_once()
        self.assertFalse(h.session.active)

    def test_cancel_after_transport_return_before_ui_delivery_is_still_unknown(self):
        h = self.harness()
        h.controller.release((3, 2))
        h.step()  # Preview accepted, commit queued.
        callback = h.queue.get(timeout=3)  # Commit returned, UI has not consumed it.
        h.controller.cancel("취소")
        self.assertIn("다시 확인", h.session.message)
        callback()
        h.drain()
        h.client.spatial_commit.assert_called_once()

    def test_cancel_before_worker_dispatch_removes_commit_authority(self):
        h = self.harness()
        h.controller.preview((3, 2))
        h.drain()
        with h.controller.worker._condition:
            h.controller.release((3, 2))
            h.controller.cancel("취소")
        h.drain()
        h.client.spatial_commit.assert_not_called()
