"""Bounded editor transport. UI/session state is touched only on the main loop."""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from threading import Condition, Thread
from time import monotonic_ns
import os
import json
import logging

from .spatial_edit import SpatialEditRequest


def trace_editor_drag(stage, **facts):
    """Discrete drag diagnostics only: callers pass identifiers, never client content."""
    safe = {key: value if type(value) in (str, int, float, bool, type(None)) else None for key, value in facts.items()}
    logging.getLogger(__name__).warning("luminophore-editor-drag %s", json.dumps({"stage": stage, **safe}, sort_keys=True))


class PreviewMetrics:
    def __init__(self):
        self.enabled = os.environ.get("LUMINOPHORE_SPATIAL_PREVIEW_METRICS") == "1"
        self.counts = {}
        self.samples = {}

    def record(self, name, duration=0):
        if self.enabled:
            self.counts[name] = self.counts.get(name, 0) + 1
            self.samples.setdefault(name, deque(maxlen=2048)).append(duration)

    def report(self):
        result = {}
        for name, count in self.counts.items():
            values = sorted(self.samples[name])
            result[name] = {"count": count, **{
                label: values[min(len(values) - 1, int((len(values) - 1) * percentile))]
                for label, percentile in (("p50_ns", .50), ("p95_ns", .95), ("p99_ns", .99))}}
        return result

    def dump(self):
        if self.enabled and self.counts:
            logging.getLogger(__name__).info("spatial-preview metrics %s", json.dumps(self.report(), sort_keys=True))
            self.counts.clear()
            self.samples.clear()


@dataclass(frozen=True)
class _Job:
    epoch: int
    kind: str
    operation: object
    completed: object
    queued_at: int


class SpatialTransportWorker:
    """One thread, one running call, at most one pending dispatch reservation.

    The condition lock linearizes invalidate against dispatch. Once a job is
    marked running, cancellation cannot claim to undo its remote mutation.
    Existing transport timeouts bound shutdown; no join blocks the GTK thread.
    """
    def __init__(self, deliver):
        self._deliver = deliver
        self._condition = Condition()
        self._epoch = 0
        self._pending = None
        self._running = None
        self._dispatched_commit_epoch = None
        self._closed = False
        self.metrics = PreviewMetrics()
        self._thread = Thread(target=self._run, name="spatial-editor-ipc", daemon=True)
        self._thread.start()

    @property
    def epoch(self):
        with self._condition:
            return self._epoch

    def submit(self, kind, operation, completed):
        with self._condition:
            if self._closed:
                return False
            # Controller coalesces desired reads before submitting. A second
            # reservation would violate its single-inflight invariant.
            if self._pending is not None:
                raise RuntimeError("editor transport already has a pending operation")
            self._pending = _Job(self._epoch, kind, operation, completed, monotonic_ns())
            self._condition.notify()
            return True

    def invalidate(self):
        with self._condition:
            self._epoch += 1
            return self._dispatched_commit_epoch is not None

    def close(self):
        with self._condition:
            self._closed = True
            self._epoch += 1
            self._pending = None
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                job, self._pending = self._pending, None
                cancelled = job.epoch != self._epoch
                if not cancelled:
                    self._running = job  # The mutation dispatch boundary.
                    if job.kind == "commit":
                        self._dispatched_commit_epoch = job.epoch
            start = monotonic_ns()
            value, error = None, None
            if not cancelled:
                try:
                    value = job.operation()
                except Exception as exc:
                    error = exc
            finished = monotonic_ns()
            with self._condition:
                self._running = None
                if self._closed:
                    return
            # Metrics are recorded on the main loop too; no UI callback here.
            def complete(job=job, value=value, error=error, cancelled=cancelled,
                         start=start, finished=finished):
                with self._condition:
                    if job.kind == "commit" and self._dispatched_commit_epoch == job.epoch:
                        self._dispatched_commit_epoch = None
                self.metrics.record("worker-wait", start - job.queued_at)
                self.metrics.record(job.kind + "-ipc", finished - start)
                self.metrics.record("delivery", monotonic_ns() - finished)
                job.completed(value, error, cancelled)
                return False
            self._deliver(complete)


class SpatialPreviewController:
    """Main-loop state machine: dragging -> release pending -> committing once.

    The immutable drag identity includes model/topology/output. Session generation
    invalidates it on close, reconnect, allocation change and external snapshots.
    Only the newest candidate sequence can qualify a final preview for commit.
    """
    def __init__(self, session, client, deliver, changed, finished):
        self.session = session
        self.client = client
        self.worker = SpatialTransportWorker(deliver)
        self._changed = changed
        self._finished = finished
        self._generation = session.generation
        self._sequence = 0
        self._wanted: SpatialEditRequest | None = None
        self._cache = None
        self._inflight = None
        self._refresh = None
        self._released = False
        self._committing = False
        self._closed = False

    @property
    def busy(self):
        return self._released or self._committing

    def _sync(self):
        if self._generation != self.session.generation:
            self.worker.invalidate()
            self._generation = self.session.generation
            self._sequence += 1
            self._wanted = self._cache = None
            self._released = self._committing = False

    def cancel(self, message=""):
        uncertain = self.worker.invalidate()
        self.session.cancel("변경 결과를 다시 확인하고 있습니다" if uncertain else message)
        self._sync()
        self.worker.metrics.dump()

    def refresh(self, completed):
        if self._closed:
            return
        self._refresh = completed  # One latest refresh, never displaces a release.
        self._sync()
        self._pump()

    def preview(self, point):
        self._sync()
        if self._closed or self.busy or self.session.drag is None:
            return
        start = monotonic_ns() if self.worker.metrics.enabled else 0
        request = self.session.drag.request_at(point) if point is not None else None
        if start:
            self.worker.metrics.record("request", monotonic_ns() - start)
        if request != self._wanted:
            if self._inflight is not None:
                self.worker.metrics.record("coalesced")
            self._sequence += 1
            self._wanted = request
            self.session.request = self.session.preview = None
            self._changed(None)
        self._pump()

    def release(self, point):
        self._sync()
        if self._closed or self.busy or self.session.drag is None:
            return
        if point is None:
            self.cancel("공간 밖의 이동은 취소했습니다")
            self._finished()
            return
        self.preview(point)
        self._released = True
        self._pump()

    def close(self):
        self.cancel()
        self._closed = True
        self._inflight = None
        self._refresh = None
        self.worker.close()

    def _start(self, kind, operation, completed):
        marker = object()
        self._inflight = marker
        def done(value, error, cancelled):
            if self._closed or self._inflight is not marker:
                return
            self._inflight = None
            self._sync()
            completed(value, error, cancelled)
            self._pump()
        self.worker.submit(kind, operation, done)

    def _pump(self):
        if self._closed or self._inflight is not None:
            return
        self._sync()
        # Observe topology/lifetime changes before qualifying a queued release.
        if self._refresh is not None:
            completed, self._refresh = self._refresh, None
            epoch = self.worker.epoch
            def refreshed(value, error, cancelled):
                if not cancelled and epoch == self.worker.epoch:
                    completed(value, error)
                    self._sync()
            self._start("snapshot", self.client.spatial_state, refreshed)
            return
        request = self._wanted
        if request is None or self.session.drag is None or self._committing:
            return
        generation, sequence = self._generation, self._sequence
        if self._cache is not None and self._cache[0] == request:
            self.worker.metrics.record("cache-hit")
            preview = self._cache[1]
            if self.session.request != request:
                self.session.accept_preview(request, preview)
                self._changed(preview)
            if not self._released:
                return
            if not preview.accepted:
                self.cancel(self.session.message)
                self._finished()
                return
            # Consume the release before queueing IPC. Never replay after timeout.
            self._committing = True
            def committed(value, error, cancelled):
                if generation != self._generation or not self._committing:
                    return
                message = ("변경 결과를 다시 확인하고 있습니다" if error or cancelled else
                           "적용했습니다" if value.accepted else "공간이 변경되었거나 적용할 수 없습니다. 다시 드래그해 주세요")
                self.cancel(message)
                self.session.refresh_required = True
                self._finished()
            self._start("commit", lambda: self.client.spatial_commit(request), committed)
            return
        def previewed(value, error, cancelled):
            if cancelled or generation != self._generation or sequence != self._sequence or request != self._wanted:
                self.worker.metrics.record("discarded")
                return
            if error:
                self.cancel("미리보기를 확인하지 못했습니다. 다시 드래그해 주세요")
                self.session.refresh_required = True
                self._finished()
                return
            preview = self.session.accept_preview(request, value)
            if not self.session.active:
                self._sync()
                self._finished()
                return
            self._cache = request, preview
            self._changed(preview)
        self._start("preview", lambda: self.client.spatial_preview(request), previewed)


class SpatialNativeDrag:
    """Only begin/cancel cross IPC. Native input owns motion and release.

    Cancellation is queued after a dispatched begin, using the original press
    identity. A delayed reply cannot acquire a later click or cancel another drag.
    """
    def __init__(self, client, deliver, changed):
        self.client = client
        self.worker = SpatialTransportWorker(deliver)
        self.changed = changed
        self.press = None
        self.pending = False
        self.cancelled = False
        self.closing = False

    def begin(self, address, snapshot, output, press):
        if self.closing or self.press is not None or self.pending:
            trace_editor_drag("transport-busy", press=press, closing=self.closing, pending=self.pending, active_press=self.press)
            return False
        trace_editor_drag("request", press=press, revision=snapshot.revision, topology=snapshot.topology_revision, output=output)
        self.press, self.pending, self.cancelled = press, True, False
        def done(value, error, cancelled):
            trace_editor_drag("reply", press=press, accepted=bool(value), error_type=type(error).__name__ if error else None,
                              cancelled=bool(cancelled or self.cancelled))
            self.pending = False
            if self.cancelled or error or cancelled:
                self._cancel_remote(press)
            elif not value:
                self.press = None
            if not self.closing:
                self.changed(bool(value) and not self.cancelled and not error)
            if self.closing and not self.pending:
                self.worker.close()
        self.worker.submit("grab-begin", lambda: self.client.spatial_grab_begin(address, snapshot.revision, snapshot.topology_revision, output, press), done)
        return True

    def finished(self):
        self.press = None

    def cancel(self):
        self.cancelled = True
        if not self.pending and self.press is not None:
            self._cancel_remote(self.press)

    def _cancel_remote(self, press):
        self.press = None
        self.pending = True
        def done(*_):
            self.pending = False
            if self.closing:
                self.worker.close()
        self.worker.submit("grab-cancel", lambda: self.client.spatial_grab_cancel(press), done)

    def close(self):
        self.closing = True
        self.cancel()
        if not self.pending:
            self.worker.close()
