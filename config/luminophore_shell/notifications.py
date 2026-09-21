from __future__ import annotations

from dataclasses import dataclass, field
import subprocess
import time
from typing import Any, Callable

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

from .config import NotificationConfig
from .state import read_json, state_dir, write_json_atomic


@dataclass
class Notification:
    id: int
    app_name: str
    app_icon: str
    summary: str
    body: str
    actions: tuple[tuple[str, str], ...]
    hints: dict[str, Any]
    expire_timeout: int
    created_at: float = field(default_factory=time.time)
    unread: bool = True

    @property
    def urgency(self) -> int:
        try:
            return int(self.hints.get("urgency", 1))
        except (TypeError, ValueError):
            return 1

    @property
    def critical(self) -> bool:
        return self.urgency >= 2

    @property
    def desktop_entry(self) -> str:
        return str(self.hints.get("desktop-entry", ""))

    @property
    def app_key(self) -> str:
        return (self.desktop_entry or self.app_name or "unknown").casefold()

    @property
    def sound_name(self) -> str:
        if bool(self.hints.get("suppress-sound", False)):
            return ""
        return str(self.hints.get("sound-name", ""))


def _actions(values: list[object]) -> tuple[tuple[str, str], ...]:
    return tuple((str(values[index]), str(values[index + 1])) for index in range(0, len(values) - 1, 2))


def notification_target_connector(focused: str, available: tuple[str, ...]) -> str:
    if focused in available:
        return focused
    return available[0] if available else ""


class NotificationManager:
    def __init__(
        self,
        config: NotificationConfig,
        changed: Callable[[], None],
        popup: Callable[[Notification], None],
    ) -> None:
        self.config = config
        self.changed = changed
        self.popup = popup
        self.notifications: dict[int, Notification] = {}
        self.history: list[Notification] = []
        self.next_id = 1
        self.fullscreen = False
        self.dnd_path = state_dir() / "dnd.json"
        raw = read_json(self.dnd_path, {})
        self.dnd = bool(raw.get("enabled", False)) if isinstance(raw, dict) else False
        self.service: NotificationService | None = None
        self.start_error = ""

    def start(self) -> bool:
        try:
            DBusGMainLoop(set_as_default=True)
            bus = dbus.SessionBus()
            name = dbus.service.BusName("org.freedesktop.Notifications", bus=bus, do_not_queue=True)
            self.service = NotificationService(name, self)
            return True
        except (dbus.DBusException, RuntimeError) as exc:
            self.start_error = str(exc)
            return False

    def set_dnd(self, enabled: bool) -> None:
        self.dnd = enabled
        write_json_atomic(self.dnd_path, {"enabled": enabled})
        self.changed()

    def set_fullscreen(self, enabled: bool) -> None:
        self.fullscreen = enabled

    def notify(
        self,
        app_name: str,
        replaces_id: int,
        app_icon: str,
        summary: str,
        body: str,
        actions: list[object],
        hints: dict[str, Any],
        expire_timeout: int,
    ) -> int:
        self.prune_history()
        notification_id = replaces_id if replaces_id and replaces_id in self.notifications else self.next_id
        if notification_id == self.next_id:
            self.next_id += 1
        notification = Notification(
            notification_id,
            app_name,
            app_icon,
            summary,
            body,
            _actions(actions),
            dict(hints),
            expire_timeout,
        )
        self.notifications[notification_id] = notification
        self.history = [item for item in self.history if item.id != notification_id]
        allowed = app_name in self.config.dnd_allowlist
        if (self.dnd or self.fullscreen) and not allowed:
            self._add_history(notification)
        else:
            self.popup(notification)
            self._play_sound(notification)
        self.changed()
        return notification_id

    def notify_hardware(self, summary: str, body: str) -> int:
        return self.notify(
            "luminophore-shell", 0, "", summary, body, [],
            {"urgency": 2, "x-luminophore-hardware-alert": True}, 0,
        )

    def popup_expired(self, notification_id: int) -> None:
        notification = self.notifications.get(notification_id)
        if notification:
            self._add_history(notification)
            self._emit_closed(notification_id, 1)

    def dismiss(self, notification_id: int, reason: int = 2) -> None:
        self.notifications.pop(notification_id, None)
        self.history = [item for item in self.history if item.id != notification_id]
        self._emit_closed(notification_id, reason)
        self.changed()

    def invoke(self, notification_id: int, action: str) -> None:
        if self.service:
            self.service.ActionInvoked(notification_id, action)
        self.dismiss(notification_id, 2)

    def clear_group(self, app_key: str) -> None:
        ids = {item.id for item in self.history if item.app_key == app_key}
        self.history = [item for item in self.history if item.id not in ids]
        for notification_id in ids:
            self.notifications.pop(notification_id, None)
        self.changed()

    def clear_all(self) -> None:
        for item in self.history:
            self.notifications.pop(item.id, None)
        self.history.clear()
        self.changed()

    def mark_all_read(self) -> None:
        self.prune_history()
        for item in self.history:
            item.unread = False
        self.changed()

    def prune_history(self, now: float | None = None, *, emit_changed: bool = True) -> bool:
        cutoff = (time.time() if now is None else now) - self.config.retention_hours * 3600
        expired = {item.id for item in self.history if item.created_at < cutoff}
        if not expired:
            return False
        self.history = [item for item in self.history if item.id not in expired]
        for notification_id in expired:
            self.notifications.pop(notification_id, None)
        if emit_changed:
            self.changed()
        return True

    def _add_history(self, notification: Notification) -> None:
        self.prune_history()
        self.history = [item for item in self.history if item.id != notification.id]
        self.history.insert(0, notification)
        del self.history[self.config.history_limit :]
        self.changed()

    def _emit_closed(self, notification_id: int, reason: int) -> None:
        if self.service:
            self.service.NotificationClosed(notification_id, reason)

    def _play_sound(self, notification: Notification) -> None:
        if not notification.sound_name:
            return
        try:
            subprocess.Popen(
                ["canberra-gtk-play", "-i", notification.sound_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass


class NotificationService(dbus.service.Object):
    def __init__(self, bus_name: dbus.service.BusName, manager: NotificationManager) -> None:
        self.manager = manager
        super().__init__(bus_name, "/org/freedesktop/Notifications")

    @dbus.service.method("org.freedesktop.Notifications", in_signature="susssasa{sv}i", out_signature="u")
    def Notify(self, app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout):
        return dbus.UInt32(self.manager.notify(str(app_name), int(replaces_id), str(app_icon), str(summary), str(body), list(actions), dict(hints), int(expire_timeout)))

    @dbus.service.method("org.freedesktop.Notifications", in_signature="u", out_signature="")
    def CloseNotification(self, notification_id):
        self.manager.dismiss(int(notification_id), 3)

    @dbus.service.method("org.freedesktop.Notifications", in_signature="", out_signature="as")
    def GetCapabilities(self):
        return ["actions", "body", "body-markup", "icon-static", "persistence", "sound"]

    @dbus.service.method("org.freedesktop.Notifications", in_signature="", out_signature="ssss")
    def GetServerInformation(self):
        return ("luminophore-shell", "msang710", "0.1.0", "1.2")

    @dbus.service.signal("org.freedesktop.Notifications", signature="uu")
    def NotificationClosed(self, notification_id, reason):
        pass

    @dbus.service.signal("org.freedesktop.Notifications", signature="us")
    def ActionInvoked(self, notification_id, action_key):
        pass
