from __future__ import annotations

import unittest

from luminophore_shell.ui.projection import FrameProjectionHandshake


class FakeWidget:
    def __init__(self) -> None:
        self.callback = None
        self.removed: list[int] = []

    def add_tick_callback(self, callback):
        self.callback = callback
        return 17

    def remove_tick_callback(self, callback_id: int) -> None:
        self.removed.append(callback_id)


class ProjectionHandshakeTests(unittest.TestCase):
    def test_retries_on_frames_until_projection_is_accepted(self) -> None:
        widget = FakeWidget()
        results = iter((False, False, True))
        handshake = FrameProjectionHandshake(widget, "luminophore-shell-launcher", lambda: next(results))

        handshake.start()
        self.assertTrue(handshake.pending)
        self.assertTrue(widget.callback(widget, object()))
        self.assertTrue(widget.callback(widget, object()))
        self.assertFalse(widget.callback(widget, object()))
        self.assertFalse(handshake.pending)

    def test_start_is_idempotent_and_cancel_removes_pending_tick(self) -> None:
        widget = FakeWidget()
        handshake = FrameProjectionHandshake(widget, "luminophore-shell-osd-DP-1", lambda: False)

        handshake.start()
        handshake.start()
        handshake.cancel()

        self.assertEqual(widget.removed, [17])
        self.assertFalse(handshake.pending)

    def test_revision_must_be_stable_before_submission(self) -> None:
        widget = FakeWidget()
        revision = 1
        submissions = 0

        def submit() -> bool:
            nonlocal submissions
            submissions += 1
            return True

        handshake = FrameProjectionHandshake(widget, "luminophore-shell-spotify", submit, lambda: revision)
        handshake.start()
        self.assertTrue(widget.callback(widget, object()))
        revision = 2
        self.assertTrue(widget.callback(widget, object()))
        self.assertFalse(widget.callback(widget, object()))
        self.assertEqual(submissions, 1)


if __name__ == "__main__":
    unittest.main()
