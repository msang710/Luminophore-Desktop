from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping


class CompositorRuntimeError(RuntimeError):
    pass


def is_luminophore_session(values: Mapping[str, str]) -> bool:
    return (
        values.get("LUMINOPHORE_COMPOSITOR") == "1"
        or any(key in values for key in ("LUMINOPHORE_COMPOSITOR_ROOT", "LUMINOPHORE_RELEASE_ROOT",
                                        "LUMINOPHORE_RELEASE_ID"))
        or "luminophore" in values.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
        or values.get("XDG_SESSION_DESKTOP", "").lower() == "luminophore"
    )


def compositor_instance(environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    values = os.environ if environ is None else environ
    luminophore = is_luminophore_session(values)
    key = "LUMINOPHORE_INSTANCE_SIGNATURE" if luminophore else "HYPRLAND_INSTANCE_SIGNATURE"
    signature = values.get(key, "")
    if not signature or signature in {".", ".."} or any(c in signature for c in "/\\\x00"):
        raise CompositorRuntimeError("compositor instance is unavailable or invalid")
    return ("luminophore" if luminophore else "hypr"), signature


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_release_manifest(raw_root: str) -> tuple[Path, dict]:
    """Validate the content identity before trusting v2 entry/environment paths."""
    try:
        root = Path(raw_root)
        if not root.is_absolute():
            raise ValueError("release root must be absolute")
        root = root.resolve(strict=True)
        path = root / "release.json"
        if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("invalid release manifest file")
        manifest = json.loads(path.read_text())
        if manifest.get("schema") != "luminophore-release/v2" or manifest.get("generation") != root.name:
            raise ValueError("invalid release identity")
        content = {key: value for key, value in manifest.items() if key != "generation"}
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        if hashlib.sha256(encoded).hexdigest() != root.name:
            raise ValueError("release manifest digest mismatch")
        return root, manifest
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise CompositorRuntimeError("LUMINOPHORE release manifest is invalid") from exc


def resolve_hyprctl(
    environ: Mapping[str, str] | None = None,
    *,
    system_path: Path = Path("/usr/bin/hyprctl"),
) -> str:
    """Resolve the control client without mixing a declared LUMINOPHORE generation."""

    values = os.environ if environ is None else environ
    if values.get("LUMINOPHORE_RELEASE_ROOT"):
        root, manifest = read_release_manifest(values["LUMINOPHORE_RELEASE_ROOT"])
        try:
            entry = manifest["entries"]["control"]
            relative = Path(entry["path"])
            if (relative.is_absolute() or ".." in relative.parts or entry["args"]
                    or values.get("LUMINOPHORE_RELEASE_ID", root.name) != root.name):
                raise ValueError("invalid control entry")
            executable = root / relative
            if executable.is_symlink() or not executable.resolve(strict=True).is_relative_to(root):
                raise ValueError("control entry escapes release")
            expected = manifest["files"][relative.as_posix()]["sha256"]
            if not os.access(executable, os.X_OK) or _sha256(executable) != expected:
                raise ValueError("control client digest mismatch")
            return str(executable)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CompositorRuntimeError("LUMINOPHORE release control client is invalid") from exc
    luminophore = is_luminophore_session(values)
    if not luminophore:
        if system_path.is_file() and os.access(system_path, os.X_OK):
            return str(system_path)
        raise CompositorRuntimeError("system compositor control client is unavailable")

    raw_root = values.get("LUMINOPHORE_COMPOSITOR_ROOT", "")
    root = Path(raw_root).expanduser()
    if not raw_root or not root.is_absolute():
        raise CompositorRuntimeError("LUMINOPHORE compositor root is unavailable")
    try:
        root = root.resolve(strict=True)
        manifest = json.loads((root / "build-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompositorRuntimeError("LUMINOPHORE runtime manifest is unavailable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "luminophore-runtime-artifact/v1":
        raise CompositorRuntimeError("LUMINOPHORE runtime manifest schema is unsupported")
    generation = manifest.get("generation")
    if not isinstance(generation, str) or root.name != generation:
        raise CompositorRuntimeError("LUMINOPHORE runtime generation identity is invalid")
    binaries = manifest.get("binaries")
    row = binaries.get("hyprctl") if isinstance(binaries, dict) else None
    expected = row.get("sha256") if isinstance(row, dict) else None
    executable = root / "bin" / "hyprctl"
    if not isinstance(expected, str) or len(expected) != 64:
        raise CompositorRuntimeError("LUMINOPHORE control client digest is unavailable")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise CompositorRuntimeError("LUMINOPHORE control client is unavailable")
    if _sha256(executable) != expected:
        raise CompositorRuntimeError("LUMINOPHORE control client digest mismatch")
    return str(executable)


__all__ = [
    "CompositorRuntimeError",
    "compositor_instance",
    "is_luminophore_session",
    "resolve_hyprctl",
]
