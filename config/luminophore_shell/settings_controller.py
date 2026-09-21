from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import (
    ConfigConflictError,
    ConfigError,
    ConfigWriteError,
    ShellConfig,
    config_digest,
    load_config,
    write_config_patch,
    restore_config_snapshot,
)
from .settings_schema import settings_values
from .settings_contract import SettingsCompletionUnknown


LOG = logging.getLogger("luminophore-shell")


class SettingsRuntimeApplyError(RuntimeError):
    """A persisted candidate could not be adopted by an external runtime."""


@dataclass(frozen=True)
class SettingsSnapshot:
    config: ShellConfig
    digest: str
    values: dict[str, Any]


@dataclass(frozen=True)
class SettingsState:
    phase: str = "idle"
    message: str = ""
    error_category: str = ""
    dirty_paths: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SettingsApplyResult:
    ok: bool
    state: SettingsState
    snapshot: SettingsSnapshot | None = None


class SettingsController:
    def __init__(
        self,
        config_path: Path,
        applied: Callable[[ShellConfig, ShellConfig, frozenset[str]], None],
        changed: Callable[[SettingsState], None] | None = None,
        *, transaction: Callable[[Mapping[str, Any], str], Mapping[str, Any]] | None = None,
        status: Callable[[str, bool], Mapping[str, Any]] | None = None,
        read_snapshot: Callable[[], tuple[ShellConfig, str]] | None = None,
    ) -> None:
        self._read_snapshot = read_snapshot
        self._transaction = transaction
        self._status = status
        self._request_id = ""
        self.config_path = config_path
        self._applied = applied
        self._changed = changed or (lambda _state: None)
        self.state = SettingsState()
        self.snapshot: SettingsSnapshot | None = None

    def _set_state(self, state: SettingsState) -> None:
        self.state = state
        self._changed(state)

    def open(self) -> SettingsSnapshot:
        config, digest = self._read_snapshot() if self._read_snapshot else (load_config(self.config_path), config_digest(self.config_path))
        snapshot = SettingsSnapshot(config, digest, settings_values(config))
        self.snapshot = snapshot
        if self.state.phase != "completion_unknown":
            self._set_state(SettingsState())
        if self._status is not None:
            response = self._status("", False)
            if response.get("found"):
                self._consume_transaction(response)
        return snapshot

    def set_dirty(self, paths: set[str] | frozenset[str]) -> None:
        self._set_state(
            SettingsState(
                self.state.phase,
                self.state.message,
                self.state.error_category,
                frozenset(paths),
            )
        )

    def _consume_transaction(self, response: Mapping[str, Any]) -> SettingsApplyResult:
        category = str(response.get("category", "runtime_apply"))
        phase = str(response.get("phase", "error"))
        self._request_id = str(response.get("request_id", self._request_id))
        unknown = phase == "completion_unknown" or category == "completion_unknown"
        ok = category in {"ok", "saved_pending_next_start", "pending_hyprland_reload"}
        if ok or unknown or category == "conflict":
            config, digest = self._read_snapshot() if self._read_snapshot else (load_config(self.config_path), config_digest(self.config_path))
            self.snapshot = SettingsSnapshot(config, digest, settings_values(config))
        message = ("적용 상태 확인 또는 미확정 요청 종료를 선택하세요" if unknown else
                   "설정을 적용했습니다" if category == "ok" else
                   str(response.get("message") or "저장했습니다. 다음 세션에서 적용됩니다") if ok else
                   "현재 설정을 다시 불러온 뒤 적용하세요" if category == "conflict" else str(response.get("message", "설정 적용 실패")))
        state = SettingsState("completion_unknown" if unknown else "saved" if ok else "error", message,
                              "" if ok else category)
        self._set_state(state)
        return SettingsApplyResult(ok, state, self.snapshot)

    def refresh_status(self, *, recover: bool = False) -> SettingsApplyResult:
        if self._status is None:
            return SettingsApplyResult(False, self.state, self.snapshot)
        response = self._status(self._request_id, recover)
        if not response.get("found"):
            response = self._status("", False)
        if response.get("found"):
            return self._consume_transaction(response)
        return SettingsApplyResult(False, self.state, self.snapshot)

    def apply(self, values: Mapping[str, Any], expected_digest: str) -> SettingsApplyResult:
        if self.state.phase in {"applying", "completion_unknown"}:
            return SettingsApplyResult(False, self.state, self.snapshot)
        old_snapshot = self.snapshot or self.open()
        base = old_snapshot.values
        changes = {
            path: value
            for path, value in values.items()
            if path in base and value != base[path]
        }
        dirty = frozenset(changes)
        if self._transaction is not None and dirty:
            return self._consume_transaction(self._transaction(changes, expected_digest))
        if not dirty:
            state = SettingsState("saved", "변경된 설정이 없습니다")
            self._set_state(state)
            return SettingsApplyResult(True, state, old_snapshot)
        self._set_state(SettingsState("applying", "설정을 검증하고 저장하는 중…", dirty_paths=dirty))
        try:
            previous_text = self.config_path.read_text(encoding="utf-8")
            new_config = write_config_patch(self.config_path, changes, expected_digest)
        except ConfigConflictError as exc:
            state = SettingsState("error", str(exc), "conflict", dirty)
            self._set_state(state)
            return SettingsApplyResult(False, state, old_snapshot)
        except (ConfigWriteError, OSError) as exc:
            LOG.warning("settings apply failed: category=write_failed error=%s", exc)
            state = SettingsState("error", "설정 파일을 저장하지 못했습니다", "write_failed", dirty)
            self._set_state(state)
            return SettingsApplyResult(False, state, old_snapshot)
        except ConfigError as exc:
            LOG.warning("settings apply rejected: category=validation error=%s", exc)
            state = SettingsState("error", str(exc), "validation", dirty)
            self._set_state(state)
            return SettingsApplyResult(False, state, old_snapshot)
        candidate_digest = config_digest(self.config_path)
        try:
            self._applied(old_snapshot.config, new_config, dirty)
        except SettingsCompletionUnknown:
            snapshot = SettingsSnapshot(new_config, config_digest(self.config_path), settings_values(new_config))
            self.snapshot = snapshot
            state = SettingsState("completion_unknown", "적용 결과를 확인하지 못했습니다. 상태 확인이 필요합니다", "completion_unknown", dirty)
            self._set_state(state)
            return SettingsApplyResult(False, state, snapshot)
        except SettingsRuntimeApplyError as exc:
            LOG.warning("settings runtime apply failed: error=%s; rolling back config", exc)
            try:
                restored_config = restore_config_snapshot(self.config_path, previous_text, candidate_digest)
            except ConfigError as rollback_exc:
                LOG.error("settings config rollback failed: %s", rollback_exc)
                state = SettingsState(
                    "error",
                    f"{exc} (설정 파일 자동 복구도 실패했습니다)",
                    "rollback_failed",
                    dirty,
                )
                self._set_state(state)
                return SettingsApplyResult(False, state, old_snapshot)
            restored = SettingsSnapshot(
                restored_config,
                config_digest(self.config_path),
                settings_values(restored_config),
            )
            self.snapshot = restored
            state = SettingsState("error", str(exc), "runtime_apply", dirty)
            self._set_state(state)
            return SettingsApplyResult(False, state, restored)
        snapshot = SettingsSnapshot(new_config, config_digest(self.config_path), settings_values(new_config))
        self.snapshot = snapshot
        state = SettingsState("saved", "설정을 적용했습니다")
        self._set_state(state)
        LOG.info("settings applied: paths=%s", ",".join(sorted(dirty)))
        return SettingsApplyResult(True, state, snapshot)
