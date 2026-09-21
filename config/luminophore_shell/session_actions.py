from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Sequence

from .session_lock import SessionLifecycleController

class SessionActionError(RuntimeError):
    pass


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


def firmware_setup_supported(which: Which = shutil.which, runner: Runner | None = None) -> bool:
    executable = which("systemctl")
    if not executable:
        return False
    call = runner or (lambda argv: subprocess.run(argv, check=False, capture_output=True, text=True, timeout=3))
    result = call((executable, "--help"))
    return result.returncode == 0 and "--firmware-setup" in (result.stdout + result.stderr)


def action_argv(action: str, *, which: Which = shutil.which) -> tuple[str, ...]:
    systemctl = which("systemctl") or "systemctl"
    if action == "reboot":
        return (systemctl, "reboot")
    if action == "poweroff":
        return (systemctl, "poweroff")
    if action == "firmware-setup":
        return (systemctl, "reboot", "--firmware-setup")
    raise ValueError("unsupported session action")


class SessionActionController:
    ACTIONS = ("lock", "suspend", "logout", "reboot", "poweroff", "firmware-setup")

    def __init__(self, runner: Runner, which: Which = shutil.which,
                 lifecycle: Callable[[str], None] | None = None) -> None:
        self.runner = runner
        self.which = which
        self.lifecycle = lifecycle or SessionLifecycleController(runner).execute

    def execute(self, action: str, *, confirmed: bool) -> None:
        if not confirmed:
            raise SessionActionError("confirmation_required")
        if action in {"lock", "suspend", "logout"}:
            try:
                self.lifecycle(action)
            except RuntimeError as exc:
                raise SessionActionError(f"action_failed:{action}") from exc
            return
        if action == "firmware-setup" and not firmware_setup_supported(self.which, self.runner):
            raise SessionActionError("firmware_setup_unsupported")
        result = self.runner(action_argv(action, which=self.which))
        if result.returncode != 0:
            raise SessionActionError(f"action_failed:{action}")
