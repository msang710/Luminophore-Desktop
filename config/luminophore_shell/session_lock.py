from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Mapping, Sequence


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
LOCK_UNIT = "luminophore-session-lock.service"


class SessionLifecycleError(RuntimeError):
    pass


def _run(runner: Runner, argv: tuple[str, ...], error: str) -> None:
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SessionLifecycleError(error) from exc
    if result.returncode != 0:
        raise SessionLifecycleError(error)


class SessionLifecycleController:
    """Issue fixed, session-scoped lock/logout/suspend requests."""

    def __init__(
        self,
        runner: Runner | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.runner = runner or (
            lambda argv: subprocess.run(argv, check=False, capture_output=True, text=True, timeout=15)
        )
        self.environ = os.environ if environ is None else environ

    def _lock(self) -> None:
        _run(
            self.runner,
            ("/usr/bin/systemctl", "--user", "start", LOCK_UNIT),
            "lock_failed",
        )

    def execute(self, action: str) -> None:
        if action == "lock":
            self._lock()
            return
        if action == "suspend":
            self._lock()
            _run(self.runner, ("/usr/bin/systemctl", "suspend"), "suspend_failed")
            return
        if action == "logout":
            session = self.environ.get("XDG_SESSION_ID", "")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]+", session) or session in {".", ".."}:
                raise SessionLifecycleError("invalid_session_id")
            _run(
                self.runner,
                ("/usr/bin/loginctl", "terminate-session", session),
                "logout_failed",
            )
            return
        raise SessionLifecycleError("unsupported_session_action")


__all__ = ["LOCK_UNIT", "SessionLifecycleController", "SessionLifecycleError"]
