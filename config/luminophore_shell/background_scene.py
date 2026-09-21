"""Versioned scene contracts. Logical roles are bound from compositor facts."""

from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re

SCHEMA = 1
MAX_PIXELS = 32_000_000
MAX_LAYERS = 8
MAX_TEXTURE_BYTES = 512 * 1024 * 1024
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("invalid_identifier")
    return value


@dataclass(frozen=True)
class SceneAsset:
    path: str
    sha256: str
    fit: str = "cover"
    focal_x: float = 0.5
    focal_y: float = 0.5

    def __post_init__(self):
        if not Path(self.path).is_absolute() or not re.fullmatch(
            r"[a-f0-9]{64}", self.sha256
        ):
            raise ValueError("invalid_asset")
        if self.fit not in {"cover", "contain"} or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
            for v in (self.focal_x, self.focal_y)
        ):
            raise ValueError("invalid_viewport")

    def validate(self):
        if not Path(self.path).is_file():
            raise ValueError("source_missing")
        if digest(Path(self.path)) != self.sha256:
            raise ValueError("source_changed")

    @classmethod
    def capture(cls, path: Path, **kwargs):
        path = path.expanduser().resolve()
        return cls(str(path), digest(path), **kwargs)


@dataclass(frozen=True)
class WallpaperScene:
    id: str
    name: str
    left: SceneAsset
    right: SceneAsset
    order: int = 0
    duration_ms: int = 600

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 120:
            raise ValueError("invalid_name")
        if (
            type(self.order) is not int
            or type(self.duration_ms) is not int
            or not 0 <= self.duration_ms <= 5000
        ):
            raise ValueError("invalid_transition")

    def to_dict(self):
        return {"schema": SCHEMA, **asdict(self)}

    @classmethod
    def parse(cls, value):
        if (
            set(value)
            != {"schema", "id", "name", "left", "right", "order", "duration_ms"}
            or type(value["schema"]) is not int
            or value["schema"] != SCHEMA
        ):
            raise ValueError("scene_schema")
        return cls(
            value["id"],
            value["name"],
            SceneAsset(**value["left"]),
            SceneAsset(**value["right"]),
            value["order"],
            value["duration_ms"],
        )


def bind_outputs(monitors):
    rows = sorted(monitors, key=lambda m: (m.x, m.name))
    if len(rows) != 2 or rows[0].x == rows[1].x or rows[0].name == rows[1].name:
        raise ValueError("unsupported_topology")
    return {"left": rows[0].name, "right": rows[1].name}
