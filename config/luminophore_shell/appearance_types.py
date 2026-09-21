from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping


APPEARANCE_PROTOCOL_VERSION = 1


class AppearanceErrorCategory(StrEnum):
    VALIDATION = "validation"
    CONFLICT = "conflict"
    BUSY = "busy"
    STALE_PREVIEW = "stale_preview"
    PROVIDER_MISSING = "provider_missing"
    PROVIDER_AMBIGUOUS = "provider_ambiguous"
    PROVIDER_INCOMPLETE = "provider_incomplete"
    COMPILER_MISSING = "compiler_missing"
    COMPILER_INCOMPATIBLE = "compiler_incompatible"
    INVALID_SOURCE = "invalid_source"
    COMPILE_TIMEOUT = "compile_timeout"
    APPLY_FAILED_ROLLED_BACK = "apply_failed_rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    COMPLETION_UNKNOWN = "completion_unknown"


class WallpaperProviderName(StrEnum):
    HYPRPAPER = "hyprpaper"
    AWWW = "awww"


class AppearanceSourceKind(StrEnum):
    IMAGE = "image"
    COLOR = "color"


class AppearanceMode(StrEnum):
    DARK = "dark"
    LIGHT = "light"


def _pairs(values: Mapping[str, str] | Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    source = values.items() if isinstance(values, Mapping) else values
    normalized = tuple(sorted((str(key), str(value)) for key, value in source))
    if len({key for key, _value in normalized}) != len(normalized):
        raise ValueError("duplicate mapping key")
    return normalized


def _require_keys(payload: Mapping[str, Any], required: set[str]) -> None:
    if set(payload) != required:
        raise ValueError(f"invalid fields: expected {sorted(required)}, got {sorted(payload)}")


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AppearanceSource:
    kind: AppearanceSourceKind
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("appearance source must not be empty")


@dataclass(frozen=True, slots=True)
class MonitorAssignment:
    connector: str
    source: AppearanceSource

    def __post_init__(self) -> None:
        if not self.connector:
            raise ValueError("connector must not be empty")


@dataclass(frozen=True, slots=True)
class WallpaperSnapshot:
    provider: WallpaperProviderName
    assignments: tuple[MonitorAssignment, ...]
    topology_digest: str
    state_digest: str

    def __post_init__(self) -> None:
        connectors = [item.connector for item in self.assignments]
        if not connectors or len(set(connectors)) != len(connectors):
            raise ValueError("wallpaper snapshot requires unique monitor assignments")
        if not self.topology_digest or not self.state_digest:
            raise ValueError("snapshot digests must not be empty")


@dataclass(frozen=True, slots=True)
class AppearanceCompileRequest:
    source: AppearanceSource
    mode: AppearanceMode
    required_outputs: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized = tuple(sorted(set(self.required_outputs)))
        if normalized != self.required_outputs or any(not item for item in normalized):
            raise ValueError("required_outputs must be unique and sorted")


@dataclass(frozen=True, slots=True)
class CompiledAppearance:
    generation_id: str
    scheme: tuple[tuple[str, str], ...]
    palette: tuple[tuple[str, str], ...]
    outputs: tuple[tuple[str, bytes], ...]

    def __post_init__(self) -> None:
        if not self.generation_id:
            raise ValueError("generation_id must not be empty")
        if tuple(sorted(self.scheme)) != self.scheme or tuple(sorted(self.palette)) != self.palette:
            raise ValueError("scheme and palette pairs must be sorted")
        names = [name for name, _payload in self.outputs]
        if names != sorted(names) or len(set(names)) != len(names) or any(not payload for _name, payload in self.outputs):
            raise ValueError("outputs must be non-empty, unique, and sorted")

    @classmethod
    def build(
        cls,
        scheme: Mapping[str, str],
        palette: Mapping[str, str],
        outputs: Mapping[str, bytes],
    ) -> "CompiledAppearance":
        scheme_pairs = _pairs(scheme)
        palette_pairs = _pairs(palette)
        output_pairs = tuple(sorted((str(name), bytes(payload)) for name, payload in outputs.items()))
        fingerprint = hashlib.sha256()
        fingerprint.update(canonical_json({"scheme": scheme_pairs, "palette": palette_pairs}))
        for name, payload in output_pairs:
            fingerprint.update(name.encode("utf-8"))
            fingerprint.update(b"\0")
            fingerprint.update(payload)
        return cls(fingerprint.hexdigest(), scheme_pairs, palette_pairs, output_pairs)

    def to_wire(self) -> dict[str, Any]:
        return {
            "version": APPEARANCE_PROTOCOL_VERSION,
            "generation_id": self.generation_id,
            "scheme": dict(self.scheme),
            "palette": dict(self.palette),
            "outputs": {name: base64.b64encode(payload).decode("ascii") for name, payload in self.outputs},
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "CompiledAppearance":
        _require_keys(payload, {"version", "generation_id", "scheme", "palette", "outputs"})
        if payload["version"] != APPEARANCE_PROTOCOL_VERSION:
            raise ValueError("unsupported appearance protocol version")
        if not all(isinstance(payload[name], dict) for name in ("scheme", "palette", "outputs")):
            raise ValueError("compiled appearance mappings are invalid")
        try:
            outputs = {
                str(name): base64.b64decode(str(value), validate=True)
                for name, value in payload["outputs"].items()
            }
        except (ValueError, TypeError) as exc:
            raise ValueError("compiled appearance output is invalid") from exc
        result = cls(
            str(payload["generation_id"]),
            _pairs(payload["scheme"]),
            _pairs(payload["palette"]),
            tuple(sorted(outputs.items())),
        )
        expected = cls.build(dict(result.scheme), dict(result.palette), dict(result.outputs))
        if result.generation_id != expected.generation_id:
            raise ValueError("compiled appearance generation mismatch")
        return result


@dataclass(frozen=True, slots=True)
class AppearancePreviewToken:
    preview_id: str
    provider: WallpaperProviderName
    monitor_sources: tuple[tuple[str, str], ...]
    topology_digest: str
    config_digest: str
    generation_ids: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not all((self.preview_id, self.topology_digest, self.config_digest)):
            raise ValueError("preview token identifiers must not be empty")
        if tuple(sorted(self.monitor_sources)) != self.monitor_sources:
            raise ValueError("monitor sources must be sorted")
        if tuple(sorted(self.generation_ids)) != self.generation_ids:
            raise ValueError("generation IDs must be sorted")


@dataclass(frozen=True, slots=True)
class TargetCapability:
    target_id: str
    available: bool
    activation: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.target_id or not self.activation:
            raise ValueError("target capability requires id and activation mode")
