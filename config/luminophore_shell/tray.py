from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop


WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
DBUSMENU_IFACE = "com.canonical.dbusmenu"


def split_item_identifier(identifier: str) -> tuple[str, str]:
    marker = identifier.find("/")
    if marker < 0:
        return identifier, "/StatusNotifierItem"
    return identifier[:marker], identifier[marker:]


@dataclass(frozen=True)
class TrayItem:
    identifier: str
    service: str
    path: str
    title: str
    status: str
    icon_name: str
    menu_path: str = ""
    item_is_menu: bool = False


@dataclass(frozen=True)
class TrayMenuEntry:
    item_id: int
    label: str = ""
    enabled: bool = True
    visible: bool = True
    separator: bool = False
    toggle_state: int = -1
    children: tuple["TrayMenuEntry", ...] = ()


def parse_menu_layout(node: object) -> tuple[TrayMenuEntry, ...]:
    try:
        _item_id, properties, children = node
    except (TypeError, ValueError):
        return ()

    entries: list[TrayMenuEntry] = []
    for child in children:
        try:
            item_id, raw_properties, grandchildren = child
            props = {str(key): value for key, value in raw_properties.items()}
        except (AttributeError, TypeError, ValueError):
            continue
        entries.append(
            TrayMenuEntry(
                item_id=int(item_id),
                label=str(props.get("label", "")).replace("_", ""),
                enabled=bool(props.get("enabled", True)),
                visible=bool(props.get("visible", True)),
                separator=str(props.get("type", "")) == "separator",
                toggle_state=int(props.get("toggle-state", -1)),
                children=parse_menu_layout((item_id, raw_properties, grandchildren)),
            )
        )
    return tuple(entries)


class LocalStatusNotifierWatcher(dbus.service.Object):
    def __init__(self, bus_name: dbus.service.BusName, registered: Callable[[str], None]) -> None:
        self.items: list[str] = []
        self.host_registered = False
        self.registered = registered
        super().__init__(bus_name, WATCHER_PATH)

    @dbus.service.method(WATCHER_IFACE, in_signature="s", out_signature="", sender_keyword="sender")
    def RegisterStatusNotifierItem(self, service_or_path, sender=None):
        value = str(service_or_path)
        identifier = f"{sender}{value}" if value.startswith("/") else value
        if "/" not in identifier:
            identifier += "/StatusNotifierItem"
        if identifier not in self.items:
            self.items.append(identifier)
            self.StatusNotifierItemRegistered(identifier)
            self.registered(identifier)

    @dbus.service.method(WATCHER_IFACE, in_signature="s", out_signature="")
    def RegisterStatusNotifierHost(self, _service):
        if not self.host_registered:
            self.host_registered = True
            self.StatusNotifierHostRegistered()

    @dbus.service.signal(WATCHER_IFACE, signature="s")
    def StatusNotifierItemRegistered(self, _service):
        pass

    @dbus.service.signal(WATCHER_IFACE, signature="s")
    def StatusNotifierItemUnregistered(self, _service):
        pass

    @dbus.service.signal(WATCHER_IFACE, signature="")
    def StatusNotifierHostRegistered(self):
        pass

    @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        if str(interface) != WATCHER_IFACE:
            raise dbus.exceptions.DBusException("unknown interface")
        values = {
            "RegisteredStatusNotifierItems": dbus.Array(self.items, signature="s"),
            "IsStatusNotifierHostRegistered": dbus.Boolean(self.host_registered),
            "ProtocolVersion": dbus.Int32(0),
        }
        if str(prop) not in values:
            raise dbus.exceptions.DBusException("unknown property")
        return values[str(prop)]

    @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if str(interface) != WATCHER_IFACE:
            return {}
        return {
            "RegisteredStatusNotifierItems": dbus.Array(self.items, signature="s"),
            "IsStatusNotifierHostRegistered": dbus.Boolean(self.host_registered),
            "ProtocolVersion": dbus.Int32(0),
        }


class StatusNotifierHost:
    def __init__(self, changed: Callable[[], None]) -> None:
        self.changed = changed
        self.bus: dbus.SessionBus | None = None
        self.items: list[TrayItem] = []
        self.identifiers: list[str] = []
        self.local_watcher: LocalStatusNotifierWatcher | None = None
        self.start_error = ""

    def start(self) -> bool:
        try:
            DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SessionBus()
            if not self._connect_existing():
                name = dbus.service.BusName(WATCHER_BUS, bus=self.bus, do_not_queue=True)
                self.local_watcher = LocalStatusNotifierWatcher(name, self._local_registered)
                self.local_watcher.host_registered = True
                self.local_watcher.StatusNotifierHostRegistered()
            return True
        except dbus.DBusException as exc:
            self.start_error = str(exc)
            return False

    def _connect_existing(self) -> bool:
        if not self.bus:
            return False
        try:
            proxy = self.bus.get_object(WATCHER_BUS, WATCHER_PATH)
            properties = dbus.Interface(proxy, PROPERTIES_IFACE)
            self.identifiers = [str(item) for item in properties.Get(WATCHER_IFACE, "RegisteredStatusNotifierItems")]
            self.bus.add_signal_receiver(self._registered, signal_name="StatusNotifierItemRegistered", dbus_interface=WATCHER_IFACE)
            self.bus.add_signal_receiver(self._unregistered, signal_name="StatusNotifierItemUnregistered", dbus_interface=WATCHER_IFACE)
            self._register_host()
            self.refresh()
            return True
        except dbus.DBusException:
            return False

    def _register_host(self) -> None:
        if not self.bus:
            return
        proxy = self.bus.get_object(WATCHER_BUS, WATCHER_PATH)
        dbus.Interface(proxy, WATCHER_IFACE).RegisterStatusNotifierHost(self.bus.get_unique_name())

    def _local_registered(self, identifier: str) -> None:
        self._registered(identifier)

    def _registered(self, identifier: object) -> None:
        value = str(identifier)
        if value not in self.identifiers:
            self.identifiers.append(value)
        self.refresh()

    def _unregistered(self, identifier: object) -> None:
        value = str(identifier)
        self.identifiers = [item for item in self.identifiers if item != value]
        self.refresh()

    def refresh(self) -> None:
        if not self.bus:
            return
        rows: list[TrayItem] = []
        alive: list[str] = []
        for identifier in self.identifiers:
            service, path = split_item_identifier(identifier)
            try:
                proxy = self.bus.get_object(service, path)
                properties = dbus.Interface(proxy, PROPERTIES_IFACE)

                def get(prop: str, default: str = "") -> str:
                    try:
                        return str(properties.Get(ITEM_IFACE, prop))
                    except dbus.DBusException:
                        return default

                menu_path = get("Menu")
                try:
                    item_is_menu = bool(properties.Get(ITEM_IFACE, "ItemIsMenu"))
                except dbus.DBusException:
                    item_is_menu = bool(menu_path and menu_path != "/")
                rows.append(
                    TrayItem(
                        identifier,
                        service,
                        path,
                        get("Title", service),
                        get("Status", "Active"),
                        get("IconName", ""),
                        menu_path,
                        item_is_menu,
                    )
                )
                alive.append(identifier)
            except dbus.DBusException:
                continue
        self.identifiers = alive
        self.items = rows
        self.changed()

    def _call(self, item: TrayItem, method: str) -> None:
        if not self.bus:
            return
        try:
            proxy = self.bus.get_object(item.service, item.path)
            getattr(dbus.Interface(proxy, ITEM_IFACE), method)(0, 0)
        except dbus.DBusException:
            self.refresh()

    def activate(self, item: TrayItem) -> None:
        self._call(item, "Activate")

    def secondary_activate(self, item: TrayItem) -> None:
        self._call(item, "SecondaryActivate")

    def context_menu(self, item: TrayItem) -> None:
        self._call(item, "ContextMenu")

    def menu_entries(self, item: TrayItem) -> tuple[TrayMenuEntry, ...]:
        if not self.bus or not item.menu_path or item.menu_path == "/":
            return ()
        try:
            proxy = self.bus.get_object(item.service, item.menu_path)
            menu = dbus.Interface(proxy, DBUSMENU_IFACE)
            menu.AboutToShow(dbus.Int32(0))
            _revision, root = menu.GetLayout(dbus.Int32(0), dbus.Int32(-1), dbus.Array([], signature="s"))
            return parse_menu_layout(root)
        except dbus.DBusException:
            return ()

    def activate_menu_entry(self, item: TrayItem, item_id: int) -> None:
        if not self.bus or not item.menu_path or item.menu_path == "/":
            return
        try:
            proxy = self.bus.get_object(item.service, item.menu_path)
            menu = dbus.Interface(proxy, DBUSMENU_IFACE)
            timestamp = int(time.monotonic() * 1000) & 0xFFFFFFFF
            menu.Event(
                dbus.Int32(item_id),
                "clicked",
                dbus.String("", variant_level=1),
                dbus.UInt32(timestamp),
            )
        except dbus.DBusException:
            self.refresh()
