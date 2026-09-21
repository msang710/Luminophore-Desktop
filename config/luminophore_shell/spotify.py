from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from gi.repository import Gio, GLib


LOG = logging.getLogger("luminophore-shell.spotify")
SPOTIFY_BUS_NAME = "org.mpris.MediaPlayer2.spotify"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"


@dataclass(frozen=True)
class SpotifySnapshot:
    connected: bool = False
    playback_status: str = "Stopped"
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""
    position_us: int = 0
    length_us: int = 0
    can_control: bool = False
    error: str = ""

    @property
    def playing(self) -> bool:
        return self.playback_status.casefold() == "playing"


def _unpack(value: object) -> object:
    return value.unpack() if isinstance(value, GLib.Variant) else value


def snapshot_from_properties(properties: dict[str, object], error: str = "") -> SpotifySnapshot:
    metadata = _unpack(properties.get("Metadata", {}))
    metadata = metadata if isinstance(metadata, dict) else {}
    artists = _unpack(metadata.get("xesam:artist", []))
    if not isinstance(artists, (list, tuple)):
        artists = []
    def text(key: str) -> str:
        value = _unpack(metadata.get(key, ""))
        return str(value)[:512] if isinstance(value, str) else ""
    def integer(value: object) -> int:
        value = _unpack(value)
        return max(0, int(value)) if isinstance(value, int) and not isinstance(value, bool) else 0
    status = _unpack(properties.get("PlaybackStatus", "Stopped"))
    return SpotifySnapshot(
        connected=True,
        playback_status=str(status) if status in {"Playing", "Paused", "Stopped"} else "Stopped",
        title=text("xesam:title"),
        artist=", ".join(str(item)[:256] for item in artists if isinstance(item, str))[:512],
        album=text("xesam:album"),
        art_url=text("mpris:artUrl"),
        position_us=integer(properties.get("Position", 0)),
        length_us=integer(metadata.get("mpris:length", 0)),
        can_control=bool(_unpack(properties.get("CanControl", False))),
        error=error,
    )


class SpotifyProvider:
    ACTIONS = {"PlayPause", "Previous", "Next"}

    def __init__(self, changed: Callable[[SpotifySnapshot], None]) -> None:
        self.changed = changed
        self.snapshot = SpotifySnapshot()
        self._watch_id = 0
        self._proxy: Gio.DBusProxy | None = None
        self._signal_id = 0
        self._poll_id = 0

    def start(self) -> None:
        if self._watch_id:
            return
        self._watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            SPOTIFY_BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._appeared,
            self._vanished,
        )

    def stop(self) -> None:
        self._disconnect_proxy()
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0

    def _appeared(self, _connection: Gio.DBusConnection, _name: str, _owner: str) -> None:
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                SPOTIFY_BUS_NAME,
                MPRIS_PATH,
                PLAYER_IFACE,
                None,
            )
        except GLib.Error:
            self._publish(SpotifySnapshot(error="spotify_dbus_unavailable"))
            return
        self._disconnect_proxy()
        self._proxy = proxy
        self._signal_id = proxy.connect("g-properties-changed", self._properties_changed)
        self._refresh()
        self._poll_id = GLib.timeout_add_seconds(1, self._poll)

    def _vanished(self, _connection: Gio.DBusConnection, _name: str) -> None:
        self._disconnect_proxy()
        self._publish(SpotifySnapshot(error="spotify_connecting"))

    def _disconnect_proxy(self) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0
        if self._proxy and self._signal_id:
            self._proxy.disconnect(self._signal_id)
        self._signal_id = 0
        self._proxy = None

    def _properties_changed(self, _proxy: Gio.DBusProxy, _changed: GLib.Variant, _invalidated: list[str]) -> None:
        self._refresh()

    def _poll(self) -> bool:
        self._refresh()
        return bool(self._proxy)

    def _refresh(self) -> None:
        if not self._proxy:
            return
        names = ("PlaybackStatus", "Metadata", "CanControl")
        properties = {name: value for name in names if (value := self._proxy.get_cached_property(name)) is not None}
        try:
            reply = self._proxy.call_sync(
                "org.freedesktop.DBus.Properties.Get",
                GLib.Variant("(ss)", (PLAYER_IFACE, "Position")),
                Gio.DBusCallFlags.NONE,
                750,
                None,
            )
            unpacked = reply.unpack()
            if unpacked:
                properties["Position"] = unpacked[0]
        except GLib.Error:
            pass
        self._publish(snapshot_from_properties(properties))

    def _publish(self, snapshot: SpotifySnapshot) -> None:
        if snapshot == self.snapshot:
            return
        self.snapshot = snapshot
        self.changed(snapshot)

    def control(self, method: str) -> bool:
        if method not in self.ACTIONS:
            raise ValueError("unsupported Spotify action")
        if not self._proxy or not self.snapshot.can_control:
            return False
        try:
            self._proxy.call_sync(method, None, Gio.DBusCallFlags.NONE, 1500, None)
            return True
        except GLib.Error:
            self._publish(SpotifySnapshot(**{**self.snapshot.__dict__, "error": "spotify_control_failed"}))
            return False
