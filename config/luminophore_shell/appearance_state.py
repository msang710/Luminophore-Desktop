from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
from typing import Callable, Mapping

from .appearance_types import CompiledAppearance, WallpaperProviderName
from .config import write_theme_source
from .state import palette_state_path, write_palette_state
from .theme import Palette


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    exists: bool
    payload: bytes
    mode: int


@dataclass(frozen=True, slots=True)
class AppearanceStateSnapshot:
    palette: FileSnapshot
    config: FileSnapshot
    native_source: str | None = None


def _snapshot(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(False, b"", 0o600)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"appearance state target is not a regular file: {path}")
    return FileSnapshot(True, path.read_bytes(), path.stat().st_mode & 0o777)


def _write_bytes_atomic(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"appearance state target is a symlink: {path}")
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


def _restore(path: Path, snapshot: FileSnapshot) -> None:
    if snapshot.exists:
        _write_bytes_atomic(path, snapshot.payload, snapshot.mode)
    else:
        path.unlink(missing_ok=True)


def _palette(compiled: CompiledAppearance) -> Palette:
    values = dict(compiled.palette)
    primary = values.get("primary", "")
    secondary = values.get("secondary", "")
    return Palette(primary.upper(), secondary.upper())


class FileAppearanceStateStore:
    """Owns palette/config persistence and runtime projection for one appearance apply."""

    def __init__(
        self,
        config_path: Path,
        runtime_apply: Callable[[], None],
        *,
        runtime_restore: Callable[[], None] | None = None,
        palette_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config_path = config_path
        self.palette_path = palette_path or palette_state_path()
        self.runtime_apply = runtime_apply
        self.runtime_restore = runtime_restore or runtime_apply
        self.clock = clock

    def snapshot(self) -> AppearanceStateSnapshot:
        from .config import _native_path, load_config
        if _native_path(self.config_path):
            return AppearanceStateSnapshot(_snapshot(self.palette_path), FileSnapshot(False, b'', 0o600),
                                           load_config(self.config_path).theme.palette_source)
        return AppearanceStateSnapshot(_snapshot(self.palette_path), _snapshot(self.config_path))

    def apply(
        self,
        compiled: Mapping[str, CompiledAppearance],
        provider: WallpaperProviderName,
    ) -> None:
        if not compiled:
            raise ValueError("compiled monitor palettes must not be empty")
        palettes = {connector: _palette(value) for connector, value in compiled.items()}
        write_palette_state(palettes, self.clock(), self.palette_path, provider=provider.value)
        write_theme_source(self.config_path, provider.value)
        self._applied_source = provider.value
        self.runtime_apply()

    def restore(self, snapshot: object) -> None:
        if not isinstance(snapshot, AppearanceStateSnapshot):
            raise ValueError("invalid appearance state snapshot")
        _restore(self.palette_path, snapshot.palette)
        if snapshot.native_source is not None:
            from .config import load_config
            current = load_config(self.config_path).theme.palette_source
            if current != snapshot.native_source:
                if current != getattr(self, '_applied_source', None):
                    raise ValueError('palette source changed after appearance apply')
                write_theme_source(self.config_path, snapshot.native_source)
        else:
            _restore(self.config_path, snapshot.config)
        self.runtime_restore()
