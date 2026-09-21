"""One in-flight read and one coalesced refresh; completions belong to GTK."""
import logging
import threading

LOG = logging.getLogger('luminophore-shell')


class WindowSnapshotReader:
    def __init__(self, client, post, completed):
        self.client, self.post, self.completed = client, post, completed
        self._condition = threading.Condition()
        self._epoch = 0
        self._pending = False
        self._closed = False
        self._thread = threading.Thread(target=self._run, name='luminophore-window-snapshot', daemon=True)
        self._thread.start()

    def request(self):
        with self._condition:
            if not self._closed:
                self._pending = True
                self._condition.notify()

    def reset(self):
        with self._condition:
            self._epoch += 1
        self.request()

    def close(self):
        with self._condition:
            self._closed = True
            self._epoch += 1
            self._condition.notify()

    @staticmethod
    def _topology(monitors):
        return sorted((m.id, m.name, m.x, m.y, m.width, m.height) for m in monitors)

    def _deliver(self, epoch, value, error):
        with self._condition:
            if self._closed or epoch != self._epoch:
                return False
        if error:
            LOG.warning('window snapshot unavailable: %s', type(error).__name__)
        elif value is not None:
            self.completed(*value)
        return False

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                epoch, self._pending = self._epoch, False
            value, error = None, None
            try:
                # A bounded second read handles topology changing between queries.
                for attempt in range(2):
                    monitors = self.client.monitors()
                    windows = self.client.windows(monitors)
                    after = self.client.monitors()
                    if self._topology(monitors) == self._topology(after):
                        value = (after, windows)
                        break
            except Exception as exc:
                error = exc
            self.post(self._deliver, epoch, value, error)
