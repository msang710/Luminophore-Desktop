"""Committed spatial visibility and one bounded background snapshot reader."""
import threading


def occupied_outputs(state):
    if (not state.committed or state.revision != state.committed_model_revision
            or state.topology_revision != state.committed_topology_revision
            or state.diagnostics or state.presentation_mode == "desktop"):
        return frozenset()
    ids = {output for window in state.windows if window.visible and window.mode == "tiled"
           for output in window.fragment_outputs}
    return frozenset(name for output, name in state.output_names if output in ids)


class VisibilityReader:
    def __init__(self, read, post):
        self.read, self.post = read, post
        self._condition = threading.Condition()
        self._pending = None
        self._closed = False
        threading.Thread(target=self._run, name="luminophore-visibility", daemon=True).start()

    def request(self, completed):
        with self._condition:
            self._pending = completed
            self._condition.notify()

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                completed, self._pending = self._pending, None
            try:
                occupied = occupied_outputs(self.read())
            except Exception:
                occupied = frozenset()
            self.post(completed, occupied)
