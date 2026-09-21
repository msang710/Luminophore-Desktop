from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import threading
from typing import Callable, Sequence


class PowerProfileError(RuntimeError):
    pass


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

PROFILE_MAP = {
    "eco": "luminophore-eco-capped",
    "balanced": "luminophore-balanced-capped",
    "gaming": "luminophore-gaming-capped",
}
PROFILE_LABELS = {
    "eco": "절전",
    "balanced": "균형",
    "gaming": "게임",
}
PROFILE_REVERSE = {target: public for public, target in PROFILE_MAP.items()}
FORBIDDEN_PROFILE_KEYS = frozenset({"boost", "min_perf_pct"})


@dataclass(frozen=True)
class PowerProfileState:
    available: bool
    active: str = ""
    profiles: tuple[str, ...] = ()
    backend: str = "tuned"
    transitioning: bool = False
    error: str = ""


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=30)


def _run(runner: Runner, argv: tuple[str, ...]) -> str:
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PowerProfileError(f"backend_unavailable:{argv[0]}") from exc
    if result.returncode != 0:
        raise PowerProfileError(f"backend_failed:{argv[0]}")
    return result.stdout


def parse_active_profile(output: str) -> str:
    match = re.search(r"(?:Current active profile|Active profile)\s*:\s*([^\s]+)", output)
    if not match:
        raise PowerProfileError("decode_failed:tuned-active")
    return match.group(1)


def parse_available_profiles(output: str) -> frozenset[str]:
    found: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^\s*[-*]\s+([a-z0-9][a-z0-9-]+)(?:\s|$)", line)
        if match:
            found.add(match.group(1))
    return frozenset(found)


def validate_profile_source(root: Path) -> tuple[str, ...]:
    expected = tuple(PROFILE_MAP.values())
    for name in expected:
        path = root / name / "tuned.conf"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PowerProfileError(f"profile_missing:{name}") from exc
        sections: set[str] = set()
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                sections.add(line[1:-1])
                continue
            key = line.split("=", 1)[0].strip()
            if key in FORBIDDEN_PROFILE_KEYS:
                raise PowerProfileError(f"forbidden_profile_key:{name}:{key}")
        if "main" not in sections or "cpu" not in sections:
            raise PowerProfileError(f"invalid_profile_sections:{name}")
        if re.search(r"^\s*include\s*=", text, re.MULTILINE):
            raise PowerProfileError(f"unsafe_profile_inheritance:{name}")
    return expected


class TunedPowerController:
    def __init__(self, runner: Runner = run_command) -> None:
        self.runner = runner
        self._transition = threading.Lock()

    def snapshot(self) -> PowerProfileState:
        try:
            active_target = parse_active_profile(_run(self.runner, ("tuned-adm", "active")))
            available_targets = parse_available_profiles(_run(self.runner, ("tuned-adm", "list")))
            profiles = tuple(public for public, target in PROFILE_MAP.items() if target in available_targets)
            active = PROFILE_REVERSE.get(active_target, "")
            if not profiles or not active:
                return PowerProfileState(False, active, profiles, error="luminophore_tuned_profiles_not_active")
            return PowerProfileState(True, active, profiles)
        except PowerProfileError as exc:
            return PowerProfileState(False, error=str(exc))

    def set_profile(self, profile: str, available: Sequence[str]) -> None:
        target = PROFILE_MAP.get(profile)
        if target is None or profile not in available:
            raise PowerProfileError("unsupported_power_profile")
        if not self._transition.acquire(blocking=False):
            raise PowerProfileError("power_profile_transition_busy")
        previous = ""
        try:
            previous = parse_active_profile(_run(self.runner, ("tuned-adm", "active")))
            _run(self.runner, ("tuned-adm", "profile", target))
            active = parse_active_profile(_run(self.runner, ("tuned-adm", "active")))
            if active != target:
                raise PowerProfileError("power_profile_activation_mismatch")
            _run(self.runner, ("tuned-adm", "verify"))
        except PowerProfileError:
            if previous and previous != target:
                try:
                    _run(self.runner, ("tuned-adm", "profile", previous))
                except PowerProfileError:
                    pass
            raise
        finally:
            self._transition.release()
