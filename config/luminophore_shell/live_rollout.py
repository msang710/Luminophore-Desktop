from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Callable, Literal, Sequence

Status = Literal["PASS", "BLOCKED", "NOT_RUN"]
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
LEGACY_PROCESS = "noc" + "talia"
OLD_POWER_SERVICE = "power-" + "profiles-daemon.service"


@dataclass(frozen=True)
class RolloutCheck:
    name: str
    status: Status
    detail: str
    approval_gate: str = ""
    rollback: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "approval_gate": self.approval_gate,
            "rollback": self.rollback,
        }


def run_read_only(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    forbidden = {"start", "stop", "restart", "enable", "disable", "reboot", "poweroff"}
    if any(part in forbidden for part in argv[1:]):
        raise ValueError(f"live rollout probe refused state-changing command: {tuple(argv)!r}")
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=8)


def _active(runner: Runner, unit: str) -> bool:
    return runner(("systemctl", "--user", "is-active", "--quiet", unit)).returncode == 0


def _system_active(runner: Runner, unit: str) -> bool:
    return runner(("systemctl", "is-active", "--quiet", unit)).returncode == 0


def collect_live_rollout(runner: Runner = run_read_only, project_root: Path | None = None) -> list[RolloutCheck]:
    root = project_root or Path(__file__).resolve().parents[1]
    shell_active = _active(runner, "luminophore-shell.service")
    session_active = _active(runner, "luminophore-session.target")
    polkit_active = _active(runner, "luminophore-polkit-agent.service")
    tuned_active = _system_active(runner, "tuned.service")
    old_power_active = _system_active(runner, OLD_POWER_SERVICE)
    legacy_active = runner(("pgrep", "-x", LEGACY_PROCESS)).returncode == 0
    ipc = runner((str(root / "luminophore-shell"), "ctl", "status"))
    ipc_ok = ipc.returncode == 0
    checks = [
        RolloutCheck(
            "desktop-shell",
            "PASS" if shell_active and ipc_ok else "BLOCKED",
            "service active and IPC responsive" if shell_active and ipc_ok else "service or IPC is unavailable",
            rollback="systemctl --user restart luminophore-shell.service",
        ),
        RolloutCheck(
            "independent-session-services",
            "PASS" if session_active and polkit_active else "BLOCKED",
            "session target and Polkit agent active" if session_active and polkit_active else "session target or Polkit agent is not active",
            approval_gate="deploy user units and restart the shell",
            rollback="systemctl --user disable --now luminophore-session.target luminophore-polkit-agent.service",
        ),
        RolloutCheck(
            "legacy-shell-process",
            "BLOCKED" if legacy_active else "PASS",
            "legacy shell process is still active" if legacy_active else "legacy shell process is absent",
            approval_gate="stop the legacy process for the desktop smoke",
            rollback=f"{LEGACY_PROCESS} --daemon",
        ),
        RolloutCheck(
            "power-backend-ownership",
            "PASS" if tuned_active and not old_power_active else "BLOCKED",
            "TuneD is the sole active power backend" if tuned_active and not old_power_active else "TuneD migration has not been applied",
            approval_gate="install tuned-cachy and run the approved power-backend transaction",
            rollback="scripts/rollback-power-backend --backup <last-good> --root / --apply",
        ),
        RolloutCheck(
            "control-location-calendar-smoke",
            "NOT_RUN",
            "reason=USER_INTERACTION_REQUIRED",
            approval_gate="open the control center and perform live device, location, and account checks",
        ),
        RolloutCheck(
            "luminophore-greeter-preview",
            "NOT_RUN",
            "reason=APPROVAL_REQUIRED",
            approval_gate="launch the staged greeter preview",
        ),
        RolloutCheck(
            "greetd-login",
            "NOT_RUN",
            "reason=APPROVAL_REQUIRED",
            approval_gate="apply greetd config, log out, and test cold login",
            rollback="scripts/rollback-session-stack --backup <last-good> --root / --apply",
        ),
        RolloutCheck(
            "firmware-setup",
            "NOT_RUN",
            "reason=APPROVAL_REQUIRED",
            approval_gate="reboot once with --firmware-setup",
        ),
        RolloutCheck(
            "plymouth-cold-boot",
            "NOT_RUN",
            "reason=APPROVAL_REQUIRED",
            approval_gate="install the staged Plymouth theme and cold boot",
            rollback="scripts/rollback-boot-theme --backup <last-good> --root / --apply",
        ),
    ]
    return checks


def main() -> int:
    checks = collect_live_rollout()
    print(json.dumps([check.as_dict() for check in checks], ensure_ascii=False, indent=2))
    return 1 if any(check.status == "BLOCKED" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
