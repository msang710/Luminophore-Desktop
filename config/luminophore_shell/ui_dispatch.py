"""Atomic admission of a request scheduled on the UI loop."""
import threading


class UiDispatchRequest:
    def __init__(self, dispatch):
        self._dispatch = dispatch
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._state = 'queued'
        self._result = None

    def invoke(self):
        with self._lock:
            if self._state != 'queued':
                return False
            self._state = 'running'
        try:
            result = self._dispatch()
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
        with self._lock:
            self._result = result
            self._state = 'completed'
            self._done.set()
        return False

    def wait(self, timeout):
        self._done.wait(timeout)
        with self._lock:
            if self._state == 'completed':
                return self._result
            if self._state in {'queued', 'cancelled'}:
                self._state = 'cancelled'
                self._done.set()
                return {'ok': False, 'category': 'cancelled', 'error': 'UI request expired before execution'}
            return {'ok': False, 'category': 'completion_unknown', 'error': 'UI request execution has started; completion is unknown'}
