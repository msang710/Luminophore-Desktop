from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable, Sequence

from .power_profiles import PROFILE_MAP, PowerProfileError, parse_active_profile, validate_profile_source


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
LEGACY_SERVICE = "power-" + "profiles-daemon.service"
LEGACY_CLIENT = "power" + "profilesctl"
TUNED_STATE_FILES = ("active_profile", "profile_mode")


class PowerBackendError(RuntimeError):
    pass


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=45)


def _call(runner: Runner, argv: tuple[str, ...], *, allowed: frozenset[int] = frozenset({0})) -> str:
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PowerBackendError(f"command_unavailable:{argv[0]}") from exc
    if result.returncode not in allowed:
        raise PowerBackendError(f"command_failed:{argv[0]}:{argv[-1]}")
    return result.stdout


def _service_flag(runner: Runner, mode: str, unit: str) -> bool:
    result = runner(("systemctl", mode, "--quiet", unit))
    return result.returncode == 0


def _service_masked(runner: Runner, unit: str) -> bool:
    result = runner(("systemctl", "is-enabled", unit))
    return result.stdout.strip() == "masked"


def _boost_snapshot(root: Path) -> dict[str, str]:
    base = root / "sys/devices/system/cpu/cpufreq"
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8").strip()
        for path in sorted(base.glob("policy*/boost"))
        if path.is_file()
    }


@dataclass(frozen=True)
class PowerBackendBackup:
    path: Path


class PowerBackendInstaller:
    def __init__(self, root: Path, runner: Runner = run_command) -> None:
        self.root = root
        self.runner = runner

    def _backup_parent(self) -> Path:
        return self.root / "var/lib/luminophore-shell/power-backend/backups"

    def _snapshot_tuned_state(self, backup: Path) -> dict[str, dict[str, int | bool]]:
        state_dir = self.root / "etc/tuned"
        saved_dir = backup / "tuned-state"
        metadata: dict[str, dict[str, int | bool]] = {}
        for name in TUNED_STATE_FILES:
            path = state_dir / name
            exists = path.is_file()
            entry: dict[str, int | bool] = {"exists": exists}
            if exists:
                stat = path.stat()
                entry.update({"mode": stat.st_mode & 0o7777, "uid": stat.st_uid, "gid": stat.st_gid})
                saved_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved_dir / name)
            metadata[name] = entry
        return metadata

    def _write_tuned_preset(self, profile: str) -> None:
        state_dir = self.root / "etc/tuned"
        state_dir.mkdir(parents=True, exist_ok=True)
        for name, value in (("active_profile", profile), ("profile_mode", "manual")):
            target = state_dir / name
            staged = state_dir / f".{name}.luminophore-shell"
            staged.write_text(value + "\n", encoding="utf-8")
            staged.chmod(0o644)
            staged.replace(target)

    def _restore_tuned_state(self, backup: Path, metadata: dict[str, object]) -> None:
        state_dir = self.root / "etc/tuned"
        state_dir.mkdir(parents=True, exist_ok=True)
        for name in TUNED_STATE_FILES:
            target = state_dir / name
            raw = metadata.get(name, {})
            entry = raw if isinstance(raw, dict) else {}
            if entry.get("exists"):
                saved = backup / "tuned-state" / name
                if not saved.is_file():
                    raise PowerBackendError("invalid_backup")
                shutil.copy2(saved, target)
                os.chmod(target, int(entry["mode"]))
                os.chown(target, int(entry["uid"]), int(entry["gid"]))
            else:
                target.unlink(missing_ok=True)

    def _clear_dangling_tuned_preset(self, removed_profiles: set[str]) -> None:
        state_dir = self.root / "etc/tuned"
        active_profile = state_dir / "active_profile"
        try:
            preset = active_profile.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if preset not in removed_profiles:
            return
        active_profile.write_text("", encoding="utf-8")
        profile_mode = state_dir / "profile_mode"
        if profile_mode.exists():
            profile_mode.write_text("", encoding="utf-8")

    def install(self, source: Path) -> PowerBackendBackup:
        names = validate_profile_source(source)
        ppd_active = _service_flag(self.runner, "is-active", LEGACY_SERVICE)
        ppd_enabled = _service_flag(self.runner, "is-enabled", LEGACY_SERVICE)
        ppd_masked = _service_masked(self.runner, LEGACY_SERVICE)
        tuned_active = _service_flag(self.runner, "is-active", "tuned.service")
        tuned_enabled = _service_flag(self.runner, "is-enabled", "tuned.service")
        legacy_profile = ""
        if ppd_active:
            legacy_profile = _call(self.runner, (LEGACY_CLIENT, "get")).strip()
        tuned_profile = ""
        if tuned_active:
            try:
                tuned_profile = parse_active_profile(_call(self.runner, ("tuned-adm", "active")))
            except (PowerBackendError, PowerProfileError):
                tuned_profile = ""
        boost = _boost_snapshot(self.root)

        backup = self._backup_parent() / f"{int(time.time_ns())}"
        backup.mkdir(parents=True, mode=0o700)
        tuned_state = self._snapshot_tuned_state(backup)
        destination = self.root / "etc/tuned/profiles"
        existing: list[str] = []
        for name in names:
            current = destination / name
            if current.exists():
                existing.append(name)
                shutil.copytree(current, backup / "profiles" / name)
        state = {
            "version": 3,
            "legacy_active": ppd_active,
            "legacy_enabled": ppd_enabled,
            "legacy_masked": ppd_masked,
            "legacy_profile": legacy_profile,
            "tuned_active": tuned_active,
            "tuned_enabled": tuned_enabled,
            "tuned_profile": tuned_profile,
            "boost": boost,
            "profiles": list(names),
            "existing_profiles": existing,
            "tuned_state": tuned_state,
        }
        (backup / "rollback.json").write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        (backup / "rollback.json").chmod(0o600)

        try:
            destination.mkdir(parents=True, exist_ok=True)
            for name in names:
                target = destination / name
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source / name, target)
            _call(self.runner, ("systemctl", "mask", "--now", LEGACY_SERVICE))
            if _service_flag(self.runner, "is-active", LEGACY_SERVICE) or not _service_masked(
                self.runner, LEGACY_SERVICE
            ):
                raise PowerBackendError("legacy_backend_not_masked")
            self._write_tuned_preset(PROFILE_MAP["balanced"])
            _call(self.runner, ("systemctl", "enable", "--now", "tuned.service"))
            _call(self.runner, ("tuned-adm", "profile", PROFILE_MAP["balanced"]))
            active = parse_active_profile(_call(self.runner, ("tuned-adm", "active")))
            if active != PROFILE_MAP["balanced"]:
                raise PowerBackendError("profile_activation_mismatch")
            _call(self.runner, ("tuned-adm", "verify"))
            if _service_flag(self.runner, "is-active", LEGACY_SERVICE):
                raise PowerBackendError("legacy_backend_reactivated")
            if _boost_snapshot(self.root) != boost:
                raise PowerBackendError("firmware_boost_state_changed")
        except (OSError, PowerBackendError, PowerProfileError) as exc:
            try:
                self.rollback(backup)
            except Exception:
                pass
            if isinstance(exc, PowerBackendError):
                raise
            raise PowerBackendError("install_failed") from exc
        return PowerBackendBackup(backup)

    def rollback(self, backup: Path) -> None:
        try:
            state = json.loads((backup / "rollback.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PowerBackendError("invalid_backup") from exc
        if state.get("version") not in {1, 2, 3}:
            raise PowerBackendError("invalid_backup")
        destination = self.root / "etc/tuned/profiles"
        _call(self.runner, ("systemctl", "disable", "--now", "tuned.service"), allowed=frozenset({0, 1, 5}))
        for name in state["profiles"]:
            target = destination / name
            if target.exists():
                shutil.rmtree(target)
            saved = backup / "profiles" / name
            if name in state["existing_profiles"] and saved.is_dir():
                shutil.copytree(saved, target)

        if state["version"] in {2, 3}:
            self._restore_tuned_state(backup, state.get("tuned_state", {}))
            removed_profiles = set(state["profiles"]) - set(state["existing_profiles"])
            self._clear_dangling_tuned_preset(removed_profiles)

        if state["tuned_enabled"] or state["tuned_active"]:
            action = "enable" if state["tuned_enabled"] else "start"
            argv = ("systemctl", action, "--now", "tuned.service") if action == "enable" else (
                "systemctl", "start", "tuned.service"
            )
            _call(self.runner, argv)
            if state["tuned_profile"]:
                _call(self.runner, ("tuned-adm", "profile", state["tuned_profile"]))

        legacy_was_masked = bool(state.get("legacy_masked", False))
        if not legacy_was_masked:
            _call(self.runner, ("systemctl", "unmask", LEGACY_SERVICE), allowed=frozenset({0, 1}))
        if not legacy_was_masked and (state["legacy_enabled"] or state["legacy_active"]):
            action = "enable" if state["legacy_enabled"] else "start"
            argv = ("systemctl", action, "--now", LEGACY_SERVICE) if action == "enable" else (
                "systemctl", "start", LEGACY_SERVICE
            )
            _call(self.runner, argv)
            if state["legacy_profile"]:
                _call(self.runner, (LEGACY_CLIENT, "set", state["legacy_profile"]))
        elif legacy_was_masked:
            _call(self.runner, ("systemctl", "mask", "--now", LEGACY_SERVICE))
        if _boost_snapshot(self.root) != state["boost"]:
            raise PowerBackendError("firmware_boost_state_changed")
