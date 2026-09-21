from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import threading
from typing import Callable, Sequence


POWER_ARGV: dict[str, tuple[str, ...]] = {
    "reboot": ("/usr/bin/systemctl", "reboot"),
    "poweroff": ("/usr/bin/systemctl", "poweroff"),
    "firmware": ("/usr/bin/systemctl", "reboot", "--firmware-setup"),
}


@dataclass(frozen=True)
class PowerResult:
    success: bool
    category: str = ""


PowerRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=10)


class GreeterPowerController:
    """Fixed-argv, single-flight power actions for the minimal login surface."""

    def __init__(self, runner: PowerRunner = _run, efi_path: Path = Path("/sys/firmware/efi")) -> None:
        self.runner = runner
        self.efi_path = efi_path
        self._lock = threading.Lock()
        self._busy = False

    def request(self, action: str) -> PowerResult:
        argv = POWER_ARGV.get(action)
        if argv is None:
            return PowerResult(False, "unsupported")
        with self._lock:
            if self._busy:
                return PowerResult(False, "busy")
            self._busy = True
        try:
            if action == "firmware" and not self.efi_path.is_dir():
                return PowerResult(False, "unsupported")
            try:
                result = self.runner(argv)
            except subprocess.TimeoutExpired:
                return PowerResult(False, "timeout")
            except OSError:
                return PowerResult(False, "backend_missing")
            return PowerResult(result.returncode == 0, "" if result.returncode == 0 else "denied_or_failed")
        finally:
            with self._lock:
                self._busy = False
