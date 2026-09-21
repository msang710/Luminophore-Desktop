from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
import tomllib
from typing import Callable, Iterable, Literal, Mapping

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from .applications import ApplicationRecord


LOG = logging.getLogger("luminophore-shell")
APP_ICON_THEME = "luminophore-shell-arcticons"
BASE_ICON_THEME = "arcticons-dark"
DEFAULT_ALIASES = {
    "com.visualstudio.code.oss": "visual-studio-code",
    "org.pulseaudio.pavucontrol": "pavucontrol",
}
_SAFE_ICON_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def normalized_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold().removesuffix(".desktop"))


def safe_icon_name(value: str) -> str:
    clean = value.strip()
    if not _SAFE_ICON_NAME.fullmatch(clean):
        raise ValueError("icon name must use freedesktop name characters")
    return clean


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return tuple(result)


@dataclass(frozen=True)
class AppIconCandidates:
    identity_keys: tuple[str, ...]
    theme_names: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedAppIcon:
    source: Gio.Icon | Gdk.Paintable | None
    origin: Literal["overlay", "arcticons", "original", "generic"]
    matched_name: str = ""
    reason: str = ""

    @property
    def themed(self) -> bool:
        return self.origin in {"overlay", "arcticons"}


def icon_candidates(
    app: ApplicationRecord,
    aliases: Mapping[str, str] | Iterable[tuple[str, str]] = (),
) -> AppIconCandidates:
    configured = {key.casefold(): value for key, value in dict(aliases).items()}
    merged = {**DEFAULT_ALIASES, **configured}
    identity_keys = _unique(
        (
            app.desktop_id,
            app.desktop_id.removesuffix(".desktop"),
            *app.icon_names,
            app.window_class,
        )
    )
    aliases_by_identity = {normalized_identity(key): value for key, value in merged.items()}
    alias_names = [
        aliases_by_identity[normalized_identity(identity)]
        for identity in identity_keys
        if normalized_identity(identity) in aliases_by_identity
    ]
    theme_names = _unique(
        (
            *alias_names,
            *app.icon_names,
            app.desktop_id,
            app.desktop_id.removesuffix(".desktop"),
            app.window_class,
        )
    )
    return AppIconCandidates(identity_keys, theme_names)


def xdg_icon_search_paths() -> tuple[Path, ...]:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
    data_dirs = [
        Path(item).expanduser()
        for item in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
        if item
    ]
    return tuple(dict.fromkeys([data_home / "icons", *(item / "icons" for item in data_dirs)]))


def theme_root(theme_name: str, search_paths: Iterable[Path] | None = None) -> Path | None:
    for root in search_paths or xdg_icon_search_paths():
        candidate = root / theme_name
        if (candidate / "index.theme").is_file():
            return candidate.resolve()
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def approved_overlay_files(overlay_root: Path | None) -> frozenset[Path]:
    if overlay_root is None:
        return frozenset()
    manifest = overlay_root.parent.parent / "luminophore-shell" / "generated-icons.toml"
    try:
        rows = tomllib.loads(manifest.read_text(encoding="utf-8")).get("icons", {})
    except (OSError, tomllib.TOMLDecodeError):
        return frozenset()
    if not isinstance(rows, dict):
        return frozenset()
    approved: set[Path] = set()
    for item in rows.values():
        if not isinstance(item, dict):
            continue
        try:
            name = safe_icon_name(str(item["target_name"]))
            expected = str(item["object_sha256"])
        except (KeyError, ValueError):
            continue
        path = overlay_root / "scalable" / "apps" / f"{name}.svg"
        try:
            if re.fullmatch(r"[0-9a-f]{64}", expected) and path.is_file() and _file_sha256(path) == expected:
                approved.add(path.resolve())
        except OSError:
            continue
    return frozenset(approved)


class ApplicationIconProvider:
    def __init__(
        self,
        display: Gdk.Display,
        theme_name: str = APP_ICON_THEME,
        aliases: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        changed: Callable[[], None] | None = None,
        search_paths: Iterable[Path] | None = None,
    ) -> None:
        self.display = display
        self.search_paths = tuple(search_paths or xdg_icon_search_paths())
        self.changed = changed or (lambda: None)
        self._cache: dict[tuple[int, str, int, int], ResolvedAppIcon] = {}
        self._warned_missing = False
        self.arcticons_count = 0
        self.overlay_count = 0
        self.fallback_count = 0
        self._theme_signals: list[tuple[Gtk.IconTheme, int]] = []
        self.theme: Gtk.IconTheme | None = None
        self.base_theme: Gtk.IconTheme | None = None
        self.theme_name = "system"
        self.aliases: dict[str, str] = {}
        self.overlay_root: Path | None = None
        self.base_root: Path | None = None
        self.approved_overlay_files: frozenset[Path] = frozenset()
        self.reconfigure(theme_name, aliases)

    @property
    def cache_entries(self) -> int:
        return len(self._cache)

    @property
    def available(self) -> bool:
        return self.theme_name != "system" and self.base_root is not None

    def reconfigure(
        self,
        theme_name: str,
        aliases: Mapping[str, str] | Iterable[tuple[str, str]],
    ) -> None:
        self.theme_name = theme_name
        self.aliases = dict(aliases)
        self.overlay_root = theme_root(APP_ICON_THEME, self.search_paths)
        self.base_root = theme_root(BASE_ICON_THEME, self.search_paths)
        self.approved_overlay_files = approved_overlay_files(self.overlay_root)
        for theme, signal in self._theme_signals:
            theme.disconnect(signal)
        self._theme_signals = []
        self.theme = None
        self.base_theme = None
        if theme_name != "system" and self.base_root is not None:
            selected = APP_ICON_THEME if self.overlay_root is not None else BASE_ICON_THEME
            theme = Gtk.IconTheme(display=self.display)
            theme.set_search_path([str(path) for path in self.search_paths])
            theme.set_theme_name(selected)
            self._theme_signals.append((theme, theme.connect("changed", self._on_theme_changed)))
            self.theme = theme
            if selected == APP_ICON_THEME:
                base_theme = Gtk.IconTheme(display=self.display)
                base_theme.set_search_path([str(path) for path in self.search_paths])
                base_theme.set_theme_name(BASE_ICON_THEME)
                self._theme_signals.append((base_theme, base_theme.connect("changed", self._on_theme_changed)))
                self.base_theme = base_theme
            else:
                self.base_theme = theme
        self._warned_missing = False
        self.invalidate()

    def _on_theme_changed(self, _theme: Gtk.IconTheme) -> None:
        self.invalidate()
        self.changed()

    def invalidate(self) -> None:
        self._cache.clear()
        self.approved_overlay_files = approved_overlay_files(self.overlay_root)
        self.arcticons_count = 0
        self.overlay_count = 0
        self.fallback_count = 0

    @staticmethod
    def _inside(path: Path, root: Path | None) -> bool:
        if root is None:
            return False
        try:
            path.resolve().relative_to(root)
            return True
        except (OSError, ValueError):
            return False

    def resolve(
        self,
        app: ApplicationRecord | None,
        size: int,
        scale: int = 1,
        catalog_revision: int = 0,
    ) -> ResolvedAppIcon:
        if app is None:
            return ResolvedAppIcon(None, "generic", reason="missing-application")
        cache_key = (catalog_revision, app.desktop_id.casefold(), max(1, size), max(1, scale))
        cached = self._cache.get(cache_key)
        if cached:
            return cached
        if self.theme_name == "system":
            result = ResolvedAppIcon(app.icon, "original", reason="system-mode")
        elif self.theme is None:
            if not self._warned_missing:
                LOG.warning("Arcticons theme is unavailable; using original application icons")
                self._warned_missing = True
            result = ResolvedAppIcon(app.icon, "original", reason="theme-unavailable")
        else:
            result = self._resolve_themed(app, size, scale)
        self._cache[cache_key] = result
        if result.origin == "overlay":
            self.overlay_count += 1
        elif result.origin == "arcticons":
            self.arcticons_count += 1
        elif result.origin == "original":
            self.fallback_count += 1
        return result

    def _resolve_themed(self, app: ApplicationRecord, size: int, scale: int) -> ResolvedAppIcon:
        assert self.theme is not None
        for name in icon_candidates(app, self.aliases).theme_names:
            if not _SAFE_ICON_NAME.fullmatch(name) or not self.theme.has_icon(name):
                continue
            paintable = self.theme.lookup_icon(
                name,
                None,
                max(1, size),
                max(1, scale),
                Gtk.TextDirection.NONE,
                Gtk.IconLookupFlags.FORCE_REGULAR,
            )
            file = paintable.get_file()
            path = Path(file.get_path()) if file and file.get_path() else None
            if path and path.resolve() in self.approved_overlay_files:
                return ResolvedAppIcon(paintable, "overlay", name)
            if path and self._inside(path, self.base_root):
                return ResolvedAppIcon(paintable, "arcticons", name)
            if path and self._inside(path, self.overlay_root) and self.base_theme and self.base_theme.has_icon(name):
                base = self.base_theme.lookup_icon(
                    name,
                    None,
                    max(1, size),
                    max(1, scale),
                    Gtk.TextDirection.NONE,
                    Gtk.IconLookupFlags.FORCE_REGULAR,
                )
                base_file = base.get_file()
                base_path = Path(base_file.get_path()) if base_file and base_file.get_path() else None
                if base_path and self._inside(base_path, self.base_root):
                    return ResolvedAppIcon(base, "arcticons", name)
        return ResolvedAppIcon(app.icon, "original", reason="unmatched")

    def source_file(self, app: ApplicationRecord, size: int = 256) -> Path | None:
        system_theme = Gtk.IconTheme.get_for_display(self.display)
        direct_names = (*app.icon_names, app.desktop_id.removesuffix(".desktop"), app.window_class)
        names = _unique(
            candidate
            for name in direct_names
            if name
            for candidate in ((name if name.endswith("-symbolic") else f"{name}-symbolic"), name)
        )
        for name in names:
            if not name or not system_theme.has_icon(name):
                continue
            paintable = system_theme.lookup_icon(
                name,
                None,
                size,
                1,
                Gtk.TextDirection.NONE,
                Gtk.IconLookupFlags.FORCE_REGULAR,
            )
            file = paintable.get_file()
            if file and file.get_path():
                path = Path(file.get_path())
                if path.is_file():
                    return path
        if isinstance(app.icon, Gio.FileIcon):
            path = Path(app.icon.get_file().get_path() or "")
            return path if path.is_file() else None
        return None

    def dispose(self) -> None:
        for theme, signal in self._theme_signals:
            theme.disconnect(signal)
        self._theme_signals = []
        self.theme = None
        self.base_theme = None
        self._cache.clear()
