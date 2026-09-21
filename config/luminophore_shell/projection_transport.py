"""Bounded, per-surface projection delivery; GTK is owned by the caller."""
from collections import OrderedDict
from dataclasses import dataclass
import logging
import threading
import time

LOG = logging.getLogger("luminophore-shell")


@dataclass(frozen=True)
class ProjectionRequest:
    name: str
    overlay: bool
    generation: str
    revision: int
    style: tuple


class ProjectionTransport:
    def __init__(self, client, post):
        self.client, self.post = client, post
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._latest = {}
        self._checks = OrderedDict()
        self._closed = False
        self._epoch = 0
        self._prefer_check = False
        self._checking = None
        self._thread = threading.Thread(target=self._run, name="luminophore-projection", daemon=True)
        self._thread.start()

    def submit(self, request, completed):
        with self._condition:
            if self._closed or (request.name not in self._latest and len(self._latest) >= 64):
                return False
            self._latest[request.name] = (request.generation, self._epoch, completed, request.revision)
            self._checks.pop(request.name, None)
            self._pending[request.name] = (request, self._epoch, time.monotonic())
            self._condition.notify()
        return True

    def reconcile(self, name, generation):
        with self._condition:
            current = self._latest.get(name)
            if self._closed or not current or current[0] != generation:
                return False
            if self._checking == (name, generation, current[1], current[3]):
                return True
            self._checks[name] = (generation, current[1], current[3])
            self._condition.notify()
        return True

    def _deliver(self, name, generation, epoch, status, elapsed=0, content_revision=None):
        with self._condition:
            current = self._latest.get(name)
            if self._closed or not current or current[:2] != (generation, epoch):
                return False
            if content_revision is not None and current[3] != content_revision:
                return False
            callback = current[2]
        LOG.debug("projection result surface=%s generation=%s state=%s elapsed_ms=%.2f", name, generation, status, elapsed)
        callback(status)
        return False

    def presented(self, namespace, generation, content_revision):
        name = namespace.removeprefix("luminophore-shell-")
        with self._condition:
            current = self._latest.get(name)
            if not current or current[0] != generation or current[3] != content_revision:
                return
            epoch = current[1]
        self.post(self._deliver, name, generation, epoch, "presented", 0, content_revision)

    def forget(self, name):
        with self._condition:
            self._latest.pop(name, None)
            self._pending.pop(name, None)
            self._checks.pop(name, None)

    def reset(self):
        with self._condition:
            self._epoch += 1
            self._latest.clear()
            self._checks.clear()
            self._pending.clear()

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._latest.clear()
            self._checks.clear()
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending or self._checks)
                if self._closed:
                    return
                if self._pending and (not self._checks or not self._prefer_check):
                    self._prefer_check = True
                    _, (request, epoch, queued) = self._pending.popitem(last=False)
                    check = None
                else:
                    check = self._checks.popitem(last=False)
                    self._prefer_check = False
                    self._checking = (check[0], *check[1])
            if check is not None:
                name, (generation, epoch, content) = check
                try:
                    status = self.client.projection_state(name, generation)
                except Exception:
                    status = "unknown"
                with self._condition:
                    self._checking = None
                self.post(self._deliver, name, generation, epoch, status, 0, content)
                continue
            start = time.monotonic()
            status = "failed"
            try:
                def valid():
                    with self._condition:
                        current = self._latest.get(request.name)
                        return not self._closed and current is not None and current[:2] == (request.generation, epoch) and current[3] == request.revision
                self.client._projection_local.valid = valid
                self.client._projection_local.direct = True
                ok = self.client.shell_projection(request.name, request.overlay, request.generation, request.revision, dict(request.style))
                status = "accepted" if ok else "failed"
            except Exception:
                # A transport failure after submission is not authority to replay.
                status = "unknown"
                LOG.exception("projection completion unknown surface=%s", request.name)
            finally:
                self.client._projection_local.direct = False
                self.client._projection_local.valid = lambda: True
            LOG.debug("projection transport surface=%s queue_ms=%.2f ipc_ms=%.2f", request.name, (start-queued)*1000, (time.monotonic()-start)*1000)
            self.post(self._deliver, request.name, request.generation, epoch, status, (time.monotonic()-queued)*1000, request.revision)
