from __future__ import annotations

import subprocess
import unittest

from luminophore_shell.ipc import IpcError
from luminophore_shell.restart import RestartError, ShellRestarter


class FakeRunner:
    def __init__(self, kill_mode: str = "control-group", main_pid: int = 100) -> None:
        self.kill_mode = kill_mode
        self.main_pid = main_pid
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **_kwargs):
        command = tuple(argv)
        self.calls.append(command)
        if "--property=KillMode" in command:
            output = f"{self.kill_mode}\n"
        elif "--property=MainPID" in command:
            output = f"{self.main_pid}\n"
        else:
            if "restart" in command:
                self.main_pid += 1000
            output = ""
        return subprocess.CompletedProcess(argv, 0, output, "")


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses

    def request(self, _payload, timeout: float):
        del timeout
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class ShellRestarterTests(unittest.TestCase):
    def test_restart_waits_for_a_different_ready_pid(self) -> None:
        runner = FakeRunner(main_pid=100)
        client = FakeClient([{"ok": True, "pid": 101}, IpcError("not ready"), {"ok": True, "pid": 101}, {"ok": True, "pid": 201}])
        clock = FakeClock()
        restarter = ShellRestarter(runner, lambda: client, clock.monotonic, clock.sleep)

        result = restarter.restart(2.0)

        self.assertEqual((result.old_pid, result.new_pid), (101, 201))
        self.assertIn(("systemctl", "--user", "restart", "luminophore-shell.service"), runner.calls)

    def test_unsafe_kill_mode_is_rejected_before_restart(self) -> None:
        runner = FakeRunner(kill_mode="none")
        restarter = ShellRestarter(runner)

        with self.assertRaisesRegex(RestartError, "안전 재시작을 거부"):
            restarter.restart()

        self.assertFalse(any("restart" in call for call in runner.calls))

    def test_packaged_policy_matches_restart_helper_without_weakening_cleanup(self):
        import configparser
        from pathlib import Path
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.read(Path(__file__).resolve().parents[1] / 'systemd/luminophore-shell.service')
        self.assertEqual(unit['Service']['KillMode'], 'control-group')
        runner = FakeRunner(kill_mode=unit['Service']['KillMode'])
        client = FakeClient([{'ok': True, 'pid': 101}, {'ok': True, 'pid': 201}])
        clock = FakeClock()
        ShellRestarter(runner, lambda: client, clock.monotonic, clock.sleep).restart(2)
        self.assertTrue(all(command[2] in {'show', 'restart'} for command in runner.calls))

    def test_legacy_process_mode_remains_supported(self):
        runner = FakeRunner(kill_mode="process")
        client = FakeClient([{"ok": True, "pid": 100}, {"ok": True, "pid": 200}])
        clock = FakeClock()
        result = ShellRestarter(runner, lambda: client, clock.monotonic, clock.sleep).restart(2)
        self.assertEqual((result.old_pid, result.new_pid), (100, 200))

    def test_unchanged_wrapper_is_not_accepted_as_restart(self):
        class UnchangedRunner(FakeRunner):
            def __call__(self, argv, **kwargs):
                result = super().__call__(argv, **kwargs)
                self.main_pid = 100
                return result
        client = FakeClient([{"ok": True, "pid": 101}, {"ok": True, "pid": 201}])
        clock = FakeClock()
        with self.assertRaisesRegex(RestartError, "준비되지 않았습니다"):
            ShellRestarter(UnchangedRunner(), lambda: client, clock.monotonic, clock.sleep).restart(0.25)

    def test_readiness_timeout_is_reported(self) -> None:
        runner = FakeRunner(main_pid=100)
        client = FakeClient([IpcError("not ready")])
        clock = FakeClock()
        restarter = ShellRestarter(runner, lambda: client, clock.monotonic, clock.sleep)

        with self.assertRaisesRegex(RestartError, "준비되지 않았습니다"):
            restarter.restart(0.25)


if __name__ == "__main__":
    unittest.main()
