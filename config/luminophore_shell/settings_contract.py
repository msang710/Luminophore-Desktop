from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


SETTINGS_PROTOCOL_VERSION = 1
MAX_SETTINGS_MESSAGE_BYTES = 64 * 1024


class SettingsCompletionUnknown(RuntimeError):
    """A dispatched mutation may have completed; do not replay or roll back."""


class SettingsRoute(StrEnum):
    HOME = "home"
    APPEARANCE = "appearance"
    WALLPAPER = "wallpaper"
    PALETTE = "palette"
    SYSTEM_THEME = "system-theme"
    COMPOSITOR = "compositor"
    MOTION = "motion"
    BINDINGS = "bindings"


class SettingsApplyPhase(StrEnum):
    VALIDATING = "validating"
    APPLYING = "applying"
    VERIFYING = "verifying"
    ROLLING_BACK = "rolling_back"
    COMPLETE = "complete"
    PENDING_NEXT_START = "pending_next_start"
    PENDING_HYPRLAND_RELOAD = "pending_hyprland_reload"
    COMPLETION_UNKNOWN = "completion_unknown"


class SettingsResultCategory(StrEnum):
    OK = "ok"
    VALIDATION = "validation"
    CONFLICT = "conflict"
    BUSY = "busy"
    RUNTIME_APPLY_FAILED_ROLLED_BACK = "runtime_apply_failed_rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    COMPLETION_UNKNOWN = "completion_unknown"
    SAVED_PENDING_NEXT_START = "saved_pending_next_start"
    PENDING_HYPRLAND_RELOAD = "pending_hyprland_reload"


class SettingsTransportCategory(StrEnum):
    CONNECTION_UNAVAILABLE = "connection_unavailable"
    COMPLETION_UNKNOWN = "completion_unknown"
    PROTOCOL_ERROR = "protocol_error"
    REMOTE_REJECTION = "remote_rejection"


def _validate_request_id(value: str) -> None:
    if not value or len(value) > 128 or any(character.isspace() for character in value):
        raise ValueError("invalid request ID")


def encode_message(payload: Mapping[str, Any]) -> bytes:
    message = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(message) > MAX_SETTINGS_MESSAGE_BYTES:
        raise ValueError("settings message exceeds size limit")
    return message


def decode_message(message: bytes) -> dict[str, Any]:
    if len(message) > MAX_SETTINGS_MESSAGE_BYTES:
        raise ValueError("settings message exceeds size limit")
    try:
        payload = json.loads(message)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid settings message") from exc
    if not isinstance(payload, dict):
        raise ValueError("settings message must be an object")
    if payload.get("version") != SETTINGS_PROTOCOL_VERSION:
        raise ValueError("unsupported settings protocol version")
    return payload


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    digest: str
    values: tuple[tuple[str, Any], ...]
    online: bool

    def __post_init__(self) -> None:
        if not self.digest:
            raise ValueError("settings digest must not be empty")
        keys = [key for key, _value in self.values]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError("settings values must be unique and sorted")

    @classmethod
    def build(cls, digest: str, values: Mapping[str, Any], online: bool) -> "SettingsSnapshot":
        return cls(digest, tuple(sorted(values.items())), online)


@dataclass(frozen=True, slots=True)
class SettingsApplyRequest:
    request_id: str
    expected_digest: str
    changes: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        _validate_request_id(self.request_id)
        if not self.expected_digest:
            raise ValueError("expected digest must not be empty")
        keys = [key for key, _value in self.changes]
        if not keys or keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError("settings changes must be non-empty, unique, and sorted")

    @classmethod
    def build(cls, request_id: str, expected_digest: str, changes: Mapping[str, Any]) -> "SettingsApplyRequest":
        return cls(request_id, expected_digest, tuple(sorted(changes.items())))


@dataclass(frozen=True, slots=True)
class SettingsApplyResult:
    request_id: str
    category: SettingsResultCategory
    phase: SettingsApplyPhase
    digest: str
    changed_paths: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        _validate_request_id(self.request_id)
        if not self.digest:
            raise ValueError("result digest must not be empty")
        if tuple(sorted(set(self.changed_paths))) != self.changed_paths:
            raise ValueError("changed paths must be unique and sorted")

    def status_wire(self) -> dict[str, Any]:
        # Deliberately excludes config values and wallpaper source paths.
        return {
            "version": SETTINGS_PROTOCOL_VERSION,
            "request_id": self.request_id,
            "category": self.category.value,
            "phase": self.phase.value,
            "digest": self.digest,
            "changed_paths": list(self.changed_paths),
            "message": self.message,
        }


class SettingsMutationError(RuntimeError):
    def __init__(self, category: SettingsResultCategory, message: str) -> None:
        super().__init__(message)
        self.category = category
