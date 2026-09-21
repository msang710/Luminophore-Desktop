from __future__ import annotations

from dataclasses import dataclass
import subprocess
import time
from typing import Callable

from .ipc import IpcClient, IpcError


SERVICE_NAME = "luminophore-shell.service"
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class RestartError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestartResult:
    old_pid: int
    new_pid: int
    status: dict[str, object]


class ShellRestarter:
    def __init__(
        self,
        runner: RunCommand = subprocess.run,
        client_factory: Callable[[], IpcClient] = IpcClient,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.client_factory = client_factory
        self.monotonic = monotonic
        self.sleeper = sleeper

    def restart(self, timeout_seconds: float = 20.0) -> RestartResult:
        if timeout_seconds <= 0:
            raise RestartError("재시작 제한 시간은 0보다 커야 합니다")
        kill_mode = self._property("KillMode")
        if kill_mode not in {"process", "control-group"}:
            raise RestartError(
                f"안전 재시작을 거부했습니다: {SERVICE_NAME} KillMode={kill_mode or 'unknown'}"
            )
        old_main_pid = self._pid_property("MainPID")
        # The packaged unit's MainPID is the generation dispatcher, not the
        # daemon answering IPC. Compare both identities; a stale old socket
        # must not count as readiness just because it differs from the wrapper.
        old_pid = 0
        try:
            previous = self.client_factory().request({"command": "status"}, timeout=0.35)
            if previous.get("ok"):
                old_pid = int(previous.get("pid", 0))
        except (IpcError, OSError, TypeError, ValueError):
            pass
        result = self._systemctl("restart", SERVICE_NAME)
        if result.returncode:
            raise RestartError("systemd가 luminophore-shell 재시작을 거부했습니다")

        deadline = self.monotonic() + timeout_seconds
        last_error = "IPC 응답 없음"
        while self.monotonic() < deadline:
            try:
                response = self.client_factory().request({"command": "status"}, timeout=0.35)
                new_pid = int(response.get("pid", 0))
                new_main_pid = self._pid_property("MainPID")
                if (response.get("ok") and new_pid > 0 and new_pid != old_pid
                        and new_main_pid > 0 and new_main_pid != old_main_pid):
                    return RestartResult(old_pid, new_pid, response)
                last_error = "새 daemon PID를 확인하지 못했습니다"
            except (IpcError, OSError, TypeError, ValueError) as exc:
                last_error = str(exc)
            self.sleeper(0.1)
        raise RestartError(f"luminophore-shell이 {timeout_seconds:g}초 안에 준비되지 않았습니다: {last_error}")

    def _property(self, name: str) -> str:
        result = self._systemctl("show", SERVICE_NAME, f"--property={name}", "--value")
        if result.returncode:
            raise RestartError(f"systemd에서 {name}을 확인하지 못했습니다")
        return result.stdout.strip()

    def _pid_property(self, name: str) -> int:
        value = self._property(name)
        try:
            return int(value)
        except ValueError as exc:
            raise RestartError(f"systemd가 잘못된 {name}을 반환했습니다") from exc

    def _systemctl(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                ("systemctl", "--user", *arguments),
                check=False,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RestartError("systemd user manager에 연결하지 못했습니다") from exc
