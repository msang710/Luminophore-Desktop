from __future__ import annotations

import subprocess
import unittest

from luminophore_shell.session_lock import SessionLifecycleController, SessionLifecycleError


class SessionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def runner(self, argv):
        self.calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def test_lock_requests_only_luminophore_lock_unit(self) -> None:
        SessionLifecycleController(self.runner, {"XDG_SESSION_ID": "3"}).execute("lock")
        self.assertEqual(self.calls, [
            ("/usr/bin/systemctl", "--user", "start", "luminophore-session-lock.service"),
        ])

    def test_suspend_requires_successful_lock_first(self) -> None:
        SessionLifecycleController(self.runner, {"XDG_SESSION_ID": "3"}).execute("suspend")
        self.assertEqual(self.calls, [
            ("/usr/bin/systemctl", "--user", "start", "luminophore-session-lock.service"),
            ("/usr/bin/systemctl", "suspend"),
        ])

    def test_lock_failure_prevents_suspend(self) -> None:
        def failed(argv):
            self.calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 1, "", "failed")
        with self.assertRaisesRegex(SessionLifecycleError, "lock_failed"):
            SessionLifecycleController(failed, {"XDG_SESSION_ID": "3"}).execute("suspend")
        self.assertEqual(len(self.calls), 1)

    def test_logout_terminates_only_current_logind_session(self) -> None:
        SessionLifecycleController(self.runner, {"XDG_SESSION_ID": "c2"}).execute("logout")
        self.assertEqual(self.calls, [("/usr/bin/loginctl", "terminate-session", "c2")])

    def test_unsafe_or_missing_session_id_is_rejected(self) -> None:
        for value in ("", "../other", "a/b", "a b"):
            with self.subTest(value=value), self.assertRaisesRegex(SessionLifecycleError, "session_id"):
                SessionLifecycleController(self.runner, {"XDG_SESSION_ID": value}).execute("logout")


if __name__ == "__main__":
    unittest.main()
