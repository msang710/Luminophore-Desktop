from __future__ import annotations

import os
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .compositor_runtime import CompositorRuntimeError, compositor_instance, resolve_hyprctl


SEMANTIC_OPTION_MAP = {
    "general:col.active_border": "primary",
    "general:col.inactive_border": "surface_container",
}


class HyprlandSettingsError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class HyprlandRuntime(Protocol):
    def read_options(self, names: tuple[str, ...]) -> Mapping[str, str]: ...



HyprctlRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _run_hyprctl(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HyprlandSettingsError("runtime_unavailable", "Hyprland settings IPC is unavailable") from exc


_RGBA = re.compile(r"^rgba\(([0-9a-fA-F]{8})\)$")
_RGB = re.compile(r"^rgb\(([0-9a-fA-F]{6})\)$")


def _normalize_runtime_color(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        rgba = _RGBA.fullmatch(text)
        if rgba:
            return f"rgba({rgba.group(1).lower()})"
        rgb = _RGB.fullmatch(text)
        if rgb:
            return f"rgba({rgb.group(1).lower()}ff)"
    if type(value) is int and 0 <= value <= 0xFFFFFFFF:
        # Hyprland's legacy numeric color form is AARRGGBB.
        argb = f"{value:08x}"
        return f"rgba({argb[2:]}{argb[:2]})"
    raise HyprlandSettingsError("runtime_incompatible", "Hyprland returned an unsupported color value")


class HyprctlSettingsRuntime:
    """Read-only native compositor palette readback."""

    def __init__(self, runner: HyprctlRunner = _run_hyprctl, timeout: float = 4.0, hyprctl: str | None = None) -> None:
        self.runner = runner
        self.timeout = timeout
        self.hyprctl = hyprctl or resolve_hyprctl()

    def available(self) -> bool:
        try:
            compositor_instance()
        except CompositorRuntimeError:
            return False
        return Path(self.hyprctl).is_file()

    def _read_one(self, name: str) -> str:
        if name not in SEMANTIC_OPTION_MAP:
            raise HyprlandSettingsError("validation", "unsupported Hyprland semantic option")
        dotted = name.replace(":", ".")
        result = self.runner((self.hyprctl, "-j", "getoption", dotted), self.timeout)
        if result.returncode:
            raise HyprlandSettingsError("runtime_unavailable", "Hyprland option query failed")
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HyprlandSettingsError("runtime_incompatible", "Hyprland option query returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HyprlandSettingsError("runtime_incompatible", "Hyprland option query returned an invalid object")
        if 'gradient' in payload:
            match = re.fullmatch(r'([0-9a-fA-F]{1,8}) 0deg', payload['gradient']) if isinstance(payload['gradient'], str) else None
            if not match:
                raise HyprlandSettingsError('runtime_incompatible', 'expected one native semantic color')
            return _normalize_runtime_color(int(match[1], 16))
        for field in ("str", "custom", "data", "int"):
            if field in payload and payload[field] not in (None, ""):
                return _normalize_runtime_color(payload[field])
        raise HyprlandSettingsError("runtime_incompatible", "Hyprland option query omitted its color value")

    def read_options(self, names: tuple[str, ...]) -> Mapping[str, str]:
        if tuple(sorted(set(names))) != names:
            raise HyprlandSettingsError("validation", "Hyprland option names must be unique and sorted")
        return {name: self._read_one(name) for name in names}



@dataclass(frozen=True, slots=True)
class SemanticPalette:
    generation_id: str
    values: tuple[tuple[str, str], ...]

    @classmethod
    def from_scheme(cls, generation_id: str, scheme: Mapping[str, str]) -> "SemanticPalette":
        if len(generation_id) != 64 or any(character not in "0123456789abcdef" for character in generation_id):
            raise ValueError("generation ID must be a lowercase SHA-256 digest")
        missing = set(SEMANTIC_OPTION_MAP.values()) - set(scheme)
        if missing:
            raise ValueError(f"semantic palette is missing tokens: {sorted(missing)}")
        values = tuple(sorted((option, _hypr_color(scheme[token])) for option, token in SEMANTIC_OPTION_MAP.items()))
        return cls(generation_id, values)


def _hypr_color(value: str) -> str:
    normalized = value.strip().removeprefix("#")
    if len(normalized) not in (6, 8) or any(character not in "0123456789abcdefABCDEF" for character in normalized):
        raise ValueError(f"invalid semantic color: {value}")
    if len(normalized) == 6:
        normalized += "ff"
    return f"rgba({normalized.lower()})"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise HyprlandSettingsError("validation", f"refusing symlink target: {path}")
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@dataclass(frozen=True, slots=True)
class HyprlandApplyResult:
    generation_id: str
    changed_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HyprlandPaletteSnapshot:
    live_values: tuple[tuple[str, str], ...]
    file_existed: bool
    file_payload: bytes


class HyprlandPaletteTransaction:
    def __init__(self, runtime: HyprlandRuntime, palette_path: Path, *, service=None) -> None:
        from .domain_client import DomainClient
        self.runtime = runtime
        self.service = service or DomainClient()

    def available(self) -> bool:
        probe = getattr(self.runtime, 'available', None)
        return bool(probe()) if callable(probe) else True

    def snapshot(self, names=None) -> HyprlandPaletteSnapshot:
        docs, digest = self.service.snapshot()
        selected = names or tuple(sorted(SEMANTIC_OPTION_MAP))
        live = dict(self.runtime.read_options(selected))
        if set(live) != set(selected):
            raise HyprlandSettingsError('validation', 'native palette readback incomplete')
        payload = json.dumps({'palette': docs['settings.toml'].get('native', {}).get('palette', {}), 'generation': digest}).encode()
        return HyprlandPaletteSnapshot(tuple(sorted(live.items())), True, payload)

    def restore(self, snapshot: HyprlandPaletteSnapshot) -> None:
        before = json.loads(snapshot.file_payload)
        docs, digest = self.service.snapshot()
        native = docs['settings.toml'].get('native', {})
        native['palette'] = before['palette']
        try:
            self.service.commit({'native': native}, digest)
        except Exception as error:
            raise HyprlandSettingsError('rollback_failed', 'native palette rollback failed') from error
        if dict(self.runtime.read_options(tuple(dict(snapshot.live_values)))) != dict(snapshot.live_values):
            raise HyprlandSettingsError('rollback_failed', 'native palette rollback mismatch')

    def apply_with_snapshot(self, palette: SemanticPalette, snapshot: HyprlandPaletteSnapshot) -> HyprlandApplyResult:
        desired = dict(palette.values)
        if dict(self.runtime.read_options(tuple(desired))) != dict(snapshot.live_values):
            raise HyprlandSettingsError('conflict', 'native palette changed after snapshot')
        docs, digest = self.service.snapshot()
        if digest != json.loads(snapshot.file_payload)['generation']:
            raise HyprlandSettingsError('conflict', 'settings generation changed after palette snapshot')
        native = docs['settings.toml'].get('native', {})
        colors = {}
        for option, token in SEMANTIC_OPTION_MAP.items():
            rgba = _normalize_runtime_color(desired[option])[5:-1]
            colors[token] = rgba[6:] + rgba[:6]
        native['palette'] = colors
        self.service.commit({'native': native}, digest)
        if dict(self.runtime.read_options(tuple(desired))) != desired:
            self.restore(snapshot)
            raise HyprlandSettingsError('apply_failed_rolled_back', 'native palette mismatch; rolled back')
        return HyprlandApplyResult(palette.generation_id, tuple(sorted(desired)))

    def apply(self, palette):
        return self.apply_with_snapshot(palette, self.snapshot(tuple(dict(palette.values))))

# Compatibility names for existing consumers. Definition and validation now
# belong to the Luminophore schema, not the Hyprland bridge.
from .owned_settings import OptionSpec as HyprlandOptionSpec, SPECS

HYPRLAND_OPTION_SPECS = {key: spec for key, spec in SPECS.items()
                         if key.startswith(("compositor.", "motion."))}


MOTION_PRESETS = {
    "fast": {"windows": 2.0, "workspaces": 3.0, "curve": "quick"},
    "balanced": {"windows": 3.0, "workspaces": 5.0, "curve": "quick"},
    "smooth": {"windows": 5.0, "workspaces": 7.0, "curve": "easeInOutCubic"},
}


def validate_hyprland_settings(values: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(values) - set(HYPRLAND_OPTION_SPECS)
    if unknown:
        raise ValueError(f"unsupported Hyprland settings: {sorted(unknown)}")
    return {key: HYPRLAND_OPTION_SPECS[key].validate(value) for key, value in values.items()}
