"""Shell integration; no external provider is touched before native presentation."""

from enum import StrEnum
import os
from pathlib import Path
import signal
import time
from .appearance_types import (
    AppearanceCompileRequest,
    AppearanceSource,
    AppearanceSourceKind,
    AppearanceMode,
)
from .appearance_state import FileAppearanceStateStore
from .scene_profile_compiler import validate_package


class NativePaletteSource(StrEnum):
    NATIVE = "native"


def apply_scene_palette(app, scene, bindings):
    compiled = {}
    for role, connector in bindings.items():
        asset = getattr(scene, role)
        path = Path(asset.path)
        if path.suffix == ".json":
            path = Path(validate_package(path)["source"])
        compiled[connector] = app.appearance_compiler.compile(
            AppearanceCompileRequest(
                AppearanceSource(AppearanceSourceKind.IMAGE, str(path)),
                AppearanceMode(app.config.appearance.mode),
                (),
            )
        )
    store = FileAppearanceStateStore(app.config.path, app._appearance_runtime_reload)
    previous = store.snapshot()
    try:
        store.apply(compiled, NativePaletteSource.NATIVE)
    except Exception:
        store.restore(previous)
        raise


def retire_external_providers():
    # Invoked only by an explicitly applied, fully PRESENTED native scene.
    # Target same-user executable identities, never arbitrary command substrings.
    targets = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            executable = (entry / "exe").readlink()
            if executable.name not in {"hyprpaper", "awww-daemon"}:
                continue
            fd = os.pidfd_open(int(entry.name))
            try:
                if (entry / "exe").readlink() != executable:
                    continue
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                targets.append(int(entry.name))
            finally:
                os.close(fd)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    deadline = time.monotonic() + 2
    while targets and time.monotonic() < deadline:
        targets = [pid for pid in targets if Path(f"/proc/{pid}/exe").exists()]
        if targets:
            time.sleep(0.05)
    if targets:
        raise RuntimeError("external_provider_still_running")
