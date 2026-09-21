from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

from luminophore_shell.live_rollout import collect_live_rollout, run_read_only


class LiveRolloutTests(unittest.TestCase):
    def test_active_independent_desktop_unlocks_only_first_three_checks(self) -> None:
        def runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
            if argv[:4] == ("systemctl", "--user", "is-active", "--quiet"):
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            if argv[:3] == ("systemctl", "is-active", "--quiet"):
                active = argv[-1] == "tuned.service"
                return subprocess.CompletedProcess(argv, 0 if active else 3, stdout="", stderr="")
            if argv[:2] == ("pgrep", "-x"):
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")

        checks = collect_live_rollout(runner, Path("/repo"))
        self.assertEqual([item.status for item in checks[:4]], ["PASS", "PASS", "PASS", "PASS"])
        self.assertTrue(all(item.status == "NOT_RUN" for item in checks[4:]))
        self.assertTrue(all(item.approval_gate for item in checks[4:]))

    def test_current_transition_blockers_are_reported_separately(self) -> None:
        def runner(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
            if argv[:4] == ("systemctl", "--user", "is-active", "--quiet"):
                active = argv[-1] == "luminophore-shell.service"
                return subprocess.CompletedProcess(argv, 0 if active else 3, stdout="", stderr="")
            if argv[:3] == ("systemctl", "is-active", "--quiet"):
                active = argv[-1] != "tuned.service"
                return subprocess.CompletedProcess(argv, 0 if active else 3, stdout="", stderr="")
            if argv[:2] == ("pgrep", "-x"):
                return subprocess.CompletedProcess(argv, 0, stdout="123\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")

        checks = collect_live_rollout(runner, Path("/repo"))
        indexed = {item.name: item for item in checks}
        self.assertEqual(indexed["desktop-shell"].status, "PASS")
        self.assertEqual(indexed["independent-session-services"].status, "BLOCKED")
        self.assertEqual(indexed["legacy-shell-process"].status, "BLOCKED")
        self.assertEqual(indexed["power-backend-ownership"].status, "BLOCKED")

    def test_probe_rejects_state_changing_commands(self) -> None:
        with self.assertRaises(ValueError):
            run_read_only(("systemctl", "--user", "restart", "luminophore-shell.service"))


if __name__ == "__main__":
    unittest.main()
