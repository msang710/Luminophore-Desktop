from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Callable, Iterable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .external_launch import AsyncLaunchController, UwsmApplicationLauncher
from .state import read_json, state_dir, write_json_atomic


LOG = logging.getLogger("luminophore-shell")


def _identity(value: str) -> str:
    value = value.casefold().removesuffix(".desktop")
    return re.sub(r"[^a-z0-9]+", "", value)


@dataclass(frozen=True)
class ApplicationAction:
    action_id: str
    name: str


@dataclass(frozen=True)
class ApplicationRecord:
    desktop_id: str
    name: str
    description: str
    executable: str
    icon: Gio.Icon | None
    info: Gio.AppInfo
    actions: tuple[ApplicationAction, ...] = ()
    window_class: str = ""
    icon_names: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return _identity(self.desktop_id)


class ApplicationCatalog:
    def __init__(
        self,
        preferred_actions: Iterable[tuple[str, str]] = (),
        external_launcher: UwsmApplicationLauncher | None = None,
    ) -> None:
        self.path = state_dir() / "app-usage.json"
        raw_usage = read_json(self.path, {})
        self.usage: dict[str, int] = {
            str(key): int(value) for key, value in raw_usage.items()
        } if isinstance(raw_usage, dict) else {}
        self.apps: list[ApplicationRecord] = []
        self._by_key: dict[str, ApplicationRecord] = {}
        self._by_desktop_id: dict[str, ApplicationRecord] = {}
        self.revision = 0
        self._preferred_actions: dict[str, str] = {}
        self.external_launcher = external_launcher or UwsmApplicationLauncher()
        self._async_launch = AsyncLaunchController(GLib.idle_add)
        self.set_preferred_actions(preferred_actions)
        self.refresh()

    def submit_launch(self, operation: Callable[[], bool], complete: Callable[[bool], None]) -> bool:
        """Share one launch slot across all launcher/taskbar surfaces.

        Existing synchronous APIs remain available to CLI and bundle callers.
        The UI callback is always dispatched onto the GLib main context.
        """
        return self._async_launch.submit(operation, complete)

    def set_preferred_actions(self, actions: Iterable[tuple[str, str]]) -> None:
        self._preferred_actions = {
            desktop_id.casefold(): action_id
            for desktop_id, action_id in actions
        }

    def refresh(self) -> None:
        """Rebuild the desktop-entry index from the current Gio app registry."""
        apps = self._load()
        self.apps = apps
        self._by_key = {app.key: app for app in apps}
        self._by_desktop_id = {app.desktop_id.casefold(): app for app in apps}
        self.revision = getattr(self, "revision", 0) + 1

    def _load(self) -> list[ApplicationRecord]:
        records: list[ApplicationRecord] = []
        seen: set[str] = set()
        for info in Gio.AppInfo.get_all():
            if not info.should_show():
                continue
            desktop_id = info.get_id() or ""
            key = _identity(desktop_id)
            if not desktop_id or not key or key in seen:
                continue
            seen.add(key)
            try:
                action_ids = tuple(info.list_actions())
                actions = tuple(
                    ApplicationAction(action_id, info.get_action_name(action_id) or action_id)
                    for action_id in action_ids
                )
            except (AttributeError, GLib.Error):
                actions = ()
            try:
                window_class = info.get_startup_wm_class() or ""
            except (AttributeError, GLib.Error):
                window_class = ""
            icon = info.get_icon()
            icon_names: tuple[str, ...] = ()
            if isinstance(icon, Gio.ThemedIcon):
                try:
                    icon_names = tuple(name for name in icon.get_names() if name)
                except (AttributeError, GLib.Error):
                    icon_names = ()
            records.append(
                ApplicationRecord(
                    desktop_id=desktop_id,
                    name=info.get_display_name() or info.get_name() or desktop_id,
                    description=info.get_description() or "",
                    executable=info.get_executable() or "",
                    icon=icon,
                    info=info,
                    actions=actions,
                    window_class=window_class,
                    icon_names=icon_names,
                )
            )
        return sorted(records, key=lambda app: app.name.casefold())

    def match_window_class(self, app_class: str) -> ApplicationRecord | None:
        target = _identity(app_class)
        if not target:
            return None
        direct = self._by_key.get(target)
        if direct:
            return direct
        candidates = [app for app in self.apps if target in app.key or app.key in target]
        return min(candidates, key=lambda app: abs(len(app.key) - len(target))) if candidates else None

    def match_desktop_id(self, desktop_id: str) -> ApplicationRecord | None:
        target = desktop_id.casefold()
        direct = getattr(self, "_by_desktop_id", {}).get(target)
        if direct:
            return direct
        without_suffix = target.removesuffix(".desktop")
        return next(
            (
                app for app in self.apps
                if app.desktop_id.casefold().removesuffix(".desktop") == without_suffix
            ),
            None,
        )

    def search(self, query: str, limit: int) -> list[ApplicationRecord]:
        needle = query.casefold().strip()

        def score(app: ApplicationRecord) -> tuple[int, int, str]:
            name = app.name.casefold()
            desktop = app.desktop_id.casefold()
            if not needle:
                match = 3
            elif name.startswith(needle):
                match = 0
            elif needle in name:
                match = 1
            elif needle in desktop or needle in app.description.casefold():
                match = 2
            else:
                match = 99
            return (match, -self.usage.get(app.desktop_id, 0), name)

        ranked = sorted(self.apps, key=score)
        return [app for app in ranked if score(app)[0] < 99][:limit]

    def launch(
        self,
        app: ApplicationRecord,
        action_id: str | None = None,
        *,
        use_preferred: bool = True,
    ) -> bool:
        selected_action = action_id
        if selected_action is None and use_preferred:
            preferred = self._preferred_actions.get(app.desktop_id.casefold())
            if preferred and any(action.action_id == preferred for action in app.actions):
                selected_action = preferred
            elif preferred:
                LOG.warning(
                    "preferred desktop action unavailable: app=%s action=%s; using default",
                    app.desktop_id,
                    preferred,
                )
        if selected_action is not None and not any(action.action_id == selected_action for action in app.actions):
            LOG.warning("desktop action unavailable: app=%s action=%s", app.desktop_id, selected_action)
            return False
        if not self.external_launcher.launch_desktop(app.desktop_id, selected_action):
            LOG.warning(
                "application launch failed: app=%s action=%s",
                app.desktop_id,
                selected_action or "default",
            )
            return False
        self.usage[app.desktop_id] = self.usage.get(app.desktop_id, 0) + 1
        try:
            write_json_atomic(self.path, self.usage)
        except OSError as exc:
            LOG.warning("application usage write failed: app=%s error=%s", app.desktop_id, exc)
        return True

    def open_uri(self, uri: str) -> bool:
        return self.external_launcher.open_uri(uri)

    def launch_argv(self, argv: Iterable[str], *, app_name: str = "") -> bool:
        return self.external_launcher.launch_argv(tuple(argv), app_name=app_name)

    def launch_for_class(self, app_class: str, *, use_preferred: bool = True) -> bool:
        app = self.match_window_class(app_class)
        if not app:
            return False
        return self.launch(app, use_preferred=use_preferred)
