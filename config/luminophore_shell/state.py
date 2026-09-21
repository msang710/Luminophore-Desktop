from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any

from .theme import Palette
from .matugen import MatugenModeTokens, MatugenScheme


@dataclass(frozen=True)
class PaletteStateEntry:
    palette: Palette
    scheme: MatugenScheme | None = None


@dataclass(frozen=True)
class PaletteState:
    captured_at: float | None
    provider: str
    entries: dict[str, PaletteStateEntry]


def _xdg_dir(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable)
    base = Path(value).expanduser() if value else Path.home() / fallback
    path = base / "luminophore-shell"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    return _xdg_dir("XDG_STATE_HOME", ".local/state")


def cache_dir() -> Path:
    return _xdg_dir("XDG_CACHE_HOME", ".cache")


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if not value:
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    path = Path(value) / "luminophore-shell"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def palette_state_path() -> Path:
    return state_dir() / "palette.json"


def system_theme_state_path() -> Path:
    return state_dir() / "system-theme.json"


def system_theme_backup_root() -> Path:
    path = state_dir() / "system-theme-backups"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def system_theme_backup_pointer(kind: str) -> Path:
    if kind not in {"current", "pending"}:
        raise ValueError("system theme backup pointer must be current or pending")
    return system_theme_backup_root() / f"{kind}.json"


def read_system_theme_backup_pointer(kind: str) -> str:
    raw = read_json(system_theme_backup_pointer(kind), {})
    generation = raw.get("generation") if isinstance(raw, dict) else None
    return generation if isinstance(generation, str) else ""


def write_system_theme_backup_pointer(kind: str, generation: str) -> None:
    write_json_atomic(system_theme_backup_pointer(kind), {"version": 1, "generation": generation})


def read_system_theme_state() -> dict[str, Any]:
    raw = read_json(system_theme_state_path(), {})
    return raw if isinstance(raw, dict) and raw.get("version") == 1 else {}


def write_system_theme_state(value: dict[str, Any]) -> None:
    write_json_atomic(system_theme_state_path(), {"version": 1, **value})


def _valid_color(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


def read_palette_state_details(path: Path | None = None) -> PaletteState:
    raw = read_json(path or palette_state_path(), {})
    if not isinstance(raw, dict) or raw.get("version") != 2:
        return PaletteState(None, "", {})
    captured = raw.get("captured_at")
    provider = raw.get("provider")
    rows = raw.get("palettes")
    if not isinstance(captured, (int, float)) or not isinstance(rows, dict):
        return PaletteState(None, "", {})
    entries: dict[str, PaletteStateEntry] = {}
    for connector, value in rows.items():
        if not isinstance(connector, str) or not isinstance(value, dict):
            continue
        primary = value.get("primary")
        secondary = value.get("secondary")
        if not _valid_color(primary) or not _valid_color(secondary):
            continue
        palette = Palette(str(primary).upper(), str(secondary).upper())
        scheme: MatugenScheme | None = None
        generation_id = value.get("generation_id")
        source_color = value.get("source_color")
        modes_raw = value.get("modes")
        render_data = value.get("render_data")
        if (
            isinstance(generation_id, str)
            and len(generation_id) == 64
            and isinstance(source_color, str)
            and _valid_color(source_color)
            and isinstance(modes_raw, dict)
            and isinstance(render_data, dict)
        ):
            modes: dict[str, MatugenModeTokens] = {}
            for mode in ("dark", "light"):
                colors = modes_raw.get(mode)
                if isinstance(colors, dict) and colors and all(
                    isinstance(name, str) and _valid_color(color)
                    for name, color in colors.items()
                ):
                    modes[mode] = MatugenModeTokens(
                        mode,
                        {str(name): str(color).upper() for name, color in colors.items()},
                    )
            if set(modes) == {"dark", "light"}:
                scheme = MatugenScheme(
                    generation_id,
                    source_color.upper(),
                    modes,
                    dict(render_data),
                )
        entries[connector] = PaletteStateEntry(palette, scheme)
    return PaletteState(float(captured), provider if isinstance(provider, str) else "", entries)


def read_palette_state(path: Path | None = None) -> tuple[float | None, dict[str, Palette]]:
    state = read_palette_state_details(path)
    return state.captured_at, {connector: entry.palette for connector, entry in state.entries.items()}


def write_palette_state(
    palettes: dict[str, Palette],
    captured_at: float,
    path: Path | None = None,
    *,
    schemes: dict[str, MatugenScheme] | None = None,
    provider: str = "",
) -> None:
    write_json_atomic(
        path or palette_state_path(),
        {
            "version": 2,
            "captured_at": captured_at,
            "provider": provider,
            "palettes": {
                connector: {
                    "primary": palette.primary,
                    "secondary": palette.secondary,
                    **(
                        {
                            "generation_id": schemes[connector].generation_id,
                            "source_color": schemes[connector].source_color,
                            "modes": {
                                mode: dict(tokens.colors)
                                for mode, tokens in schemes[connector].modes.items()
                            },
                            "render_data": dict(schemes[connector].render_data),
                        }
                        if schemes is not None and connector in schemes else {}
                    ),
                }
                for connector, palette in sorted(palettes.items())
            },
        },
    )
