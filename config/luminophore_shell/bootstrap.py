from __future__ import annotations

import os
from pathlib import Path


_release_root = os.environ.get("LUMINOPHORE_RELEASE_ROOT")
_fontconfig = None
LAYER_SHELL = (str(Path(_release_root) / "lib/libgtk4-layer-shell.so.0")
               if _release_root else "/usr/lib/libgtk4-layer-shell.so")


def internal_python_environment() -> dict[str, str]:
    """Restore declared private paths for an internal worker, without export."""
    result = dict(os.environ)
    if not _release_root:
        return result
    from .compositor_runtime import read_release_manifest
    root, manifest = read_release_manifest(_release_root)
    allowed = {"PYTHONHOME", "GI_TYPELIB_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "FONTCONFIG_FILE", "QT_PLUGIN_PATH"}
    for key, value in manifest["runtime_env"].items():
        if key not in allowed:
            raise RuntimeError("unsupported private helper environment")
        relative = Path(value)
        path = (root / relative).resolve(strict=True)
        correct_type = path.is_file() if key == "FONTCONFIG_FILE" else path.is_dir()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root) or not correct_type:
            raise RuntimeError("private helper environment escapes release")
        result[key] = str(path)
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    result["PYTHONNOUSERSITE"] = "1"
    return result


def configure_release() -> None:
    """Keep private search paths in this Python process, not its child apps."""
    if not _release_root:
        return
    root = Path(_release_root).resolve(strict=True)
    import gi
    repository = gi.Repository.get_default()
    repository.prepend_library_path(str(root / "lib"))
    typelib = os.environ.get("GI_TYPELIB_PATH")
    if typelib:
        path = Path(typelib).resolve(strict=True)
        if not path.is_relative_to(root):
            raise RuntimeError("typelib path escapes the selected release")
        repository.prepend_search_path(str(path))
    if os.environ.get("GIO_MODULE_DIR") or os.environ.get("GSETTINGS_SCHEMA_DIR"):
        from gi.repository import Gio
        for name in ("GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR"):
            if name in os.environ and not Path(os.environ[name]).resolve(strict=True).is_relative_to(root):
                raise RuntimeError("resource path escapes the selected release")
        if os.environ.get("GIO_MODULE_DIR"):
            # The default resolver registers GIO's built-in extension points
            # before discovering modules. A manual scan/load can register the
            # same dynamic GTypes twice and leave TLS permanently unavailable.
            if not Gio.TlsBackend.get_default().supports_tls():
                raise RuntimeError("private GIO TLS backend is unavailable")
            Gio.ProxyResolver.get_default()
            Gio.NetworkMonitor.get_default()
        if os.environ.get("GSETTINGS_SCHEMA_DIR"):
            Gio.SettingsSchemaSource.get_default()
    if os.environ.get("FONTCONFIG_FILE"):
        global _fontconfig
        import ctypes
        path = Path(os.environ["FONTCONFIG_FILE"]).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError("fontconfig file escapes the selected release")
        _fontconfig = ctypes.CDLL(str(root / "lib/libfontconfig.so.1"))
        _fontconfig.FcInitLoadConfigAndFonts.argtypes = []
        _fontconfig.FcInitLoadConfigAndFonts.restype = ctypes.c_void_p
        _fontconfig.FcConfigSetCurrent.argtypes = [ctypes.c_void_p]
        _fontconfig.FcConfigSetCurrent.restype = ctypes.c_int
        _fontconfig.FcConfigDestroy.argtypes = [ctypes.c_void_p]
        _fontconfig.FcConfigDestroy.restype = None
        config = _fontconfig.FcInitLoadConfigAndFonts()
        if not config:
            raise RuntimeError("private fontconfig initialization failed")
        try:
            if not _fontconfig.FcConfigSetCurrent(config):
                raise RuntimeError("private fontconfig activation failed")
        finally:
            _fontconfig.FcConfigDestroy(config)
    # Python sys.path/prefix are initialized before the entry script. GI has
    # its own in-process paths now, so subprocess and D-Bus-launched apps do
    # not inherit the private Python/GI runtime from this shell.
    for name in ("PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR",
                 "FONTCONFIG_FILE", "FONTCONFIG_PATH", "QT_PLUGIN_PATH"):
        os.environ.pop(name, None)


def preload_entries(value: str | None) -> tuple[str, ...]:
    return tuple(entry for entry in (value or "").split(":") if entry)


def with_preload(entries: tuple[str, ...], library: str) -> str:
    if library in entries:
        return ":".join(entries)
    return ":".join((library, *entries))


def without_preload(entries: tuple[str, ...], library: str) -> str | None:
    remaining = tuple(entry for entry in entries if entry != library)
    return ":".join(remaining) if remaining else None
