from __future__ import annotations

from dataclasses import dataclass
from threading import Thread
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from ..spotify import SpotifySnapshot


@dataclass(frozen=True)
class CompactSpotifyMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""


def compact_spotify_metadata(
    snapshot: SpotifySnapshot,
    previous: CompactSpotifyMetadata = CompactSpotifyMetadata(),
) -> CompactSpotifyMetadata:
    current = CompactSpotifyMetadata(snapshot.title, snapshot.artist, snapshot.album)
    return current if any((current.title, current.artist, current.album)) else previous


def expanded_artist_album(snapshot: SpotifySnapshot) -> str:
    artist = snapshot.artist or "아티스트 정보 없음"
    album = snapshot.album or "앨범 정보 없음"
    return f"{artist} - {album}"


def _button(icon: str, callback) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon)
    button.add_css_class("luminophore-button")
    button.connect("clicked", lambda _button: callback())
    return button


class SpotifyView:
    _MAX_ART_BYTES = 8 * 1024 * 1024
    _ART_SIZE = 112

    def __init__(self, toggle, previous, play_pause, next_track) -> None:
        self._toggle = toggle
        self._compact_metadata = CompactSpotifyMetadata()
        self.compact_title = Gtk.Label(label="재생 정보 없음", xalign=0, ellipsize=3)
        self.compact_title.add_css_class("weather-clock")
        self.compact_title.add_css_class("luminophore-key-primary")
        self.compact_title.set_hexpand(True)
        self.compact_title.set_max_width_chars(18)
        self.compact_artist = Gtk.Label(label="아티스트 정보 없음", xalign=1, ellipsize=3)
        self.compact_artist.add_css_class("weather-temperature")
        self.compact_artist.add_css_class("luminophore-key-secondary")
        self.compact_artist.set_max_width_chars(24)
        self.compact_album = Gtk.Label(label="앨범 정보 없음", xalign=1, ellipsize=3)
        self.compact_album.add_css_class("weather-temperature")
        self.compact_album.add_css_class("luminophore-key-secondary")
        self.compact_album.set_max_width_chars(24)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        details.set_halign(Gtk.Align.END)
        details.set_valign(Gtk.Align.CENTER)
        details.append(self.compact_artist)
        details.append(self.compact_album)
        compact = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        compact.append(self.compact_title)
        compact.append(details)
        self.collapsed = Gtk.Button(child=compact)
        self.collapsed.add_css_class("luminophore-button")
        self.collapsed.add_css_class("spotify-compact")
        self.collapsed.connect("clicked", lambda _button: toggle())

        self.title = Gtk.Label(label="Spotify 연결 중", xalign=0, ellipsize=3)
        self.title.add_css_class("section-title")
        self.title.add_css_class("luminophore-key-primary")
        self.artist_album = Gtk.Label(label="", xalign=0, ellipsize=3)
        self.artist_album.add_css_class("luminophore-key-secondary")
        self.progress = Gtk.ProgressBar(show_text=False)
        self.progress.add_css_class("spotify-progress")
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_halign(Gtk.Align.CENTER)
        self.previous = _button("media-skip-backward-symbolic", previous)
        self.play = _button("media-playback-start-symbolic", play_pause)
        self.next = _button("media-skip-forward-symbolic", next_track)
        controls.append(self.previous)
        controls.append(self.play)
        controls.append(self.next)

        information = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        information.set_hexpand(True)
        information.append(self.title)
        information.append(self.artist_album)
        information.append(controls)
        information.append(self.progress)

        self.album_art = Gtk.Picture()
        self.album_art.set_content_fit(Gtk.ContentFit.COVER)
        self.album_art.set_can_shrink(True)
        self.album_art.set_size_request(self._ART_SIZE, self._ART_SIZE)
        self.album_art.add_css_class("spotify-album-art")
        placeholder = Gtk.Image.new_from_icon_name("media-optical-symbolic")
        placeholder.add_css_class("luminophore-symbol-secondary")
        placeholder.set_pixel_size(36)
        self.album_art_stack = Gtk.Stack()
        self.album_art_stack.set_size_request(self._ART_SIZE, self._ART_SIZE)
        self.album_art_stack.add_named(placeholder, "placeholder")
        self.album_art_stack.add_named(self.album_art, "art")
        self.album_art_stack.set_visible_child_name("placeholder")
        self._art_url = ""
        self._art_request = 0

        self.expanded = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.expanded.add_css_class("spotify-expanded")
        self.expanded.append(information)
        self.expanded.append(self.album_art_stack)

    def _update_album_art(self, art_url: str) -> None:
        if art_url == self._art_url:
            return
        self._art_url = art_url
        self._art_request += 1
        request = self._art_request
        if not art_url:
            self.album_art.set_paintable(None)
            self.album_art_stack.set_visible_child_name("placeholder")
            return
        if urlparse(art_url).scheme not in {"https", "file"}:
            self.album_art.set_paintable(None)
            self.album_art_stack.set_visible_child_name("placeholder")
            return
        Thread(
            target=self._load_album_art,
            args=(art_url, request),
            name="luminophore-spotify-art",
            daemon=True,
        ).start()

    def _load_album_art(self, art_url: str, request: int) -> None:
        if request != self._art_request:
            return
        try:
            resource = Request(art_url, headers={"User-Agent": "LuminophoreShell/1"})
            with urlopen(resource, timeout=5) as response:
                content = response.read(self._MAX_ART_BYTES + 1)
            if len(content) > self._MAX_ART_BYTES:
                raise ValueError("invalid album art response")
        except (OSError, URLError, ValueError):
            content = b""
        GLib.idle_add(self._apply_album_art, request, content)

    def _apply_album_art(self, request: int, content: bytes) -> bool:
        if request != self._art_request:
            return GLib.SOURCE_REMOVE
        try:
            if not content:
                raise ValueError("empty album art response")
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(content)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf is None:
                raise ValueError("invalid album art image")
            scaled = pixbuf.scale_simple(
                self._ART_SIZE,
                self._ART_SIZE,
                GdkPixbuf.InterpType.BILINEAR,
            )
            if scaled is None:
                raise ValueError("album art scaling failed")
            texture = Gdk.Texture.new_for_pixbuf(scaled)
        except (GLib.Error, ValueError):
            self.album_art.set_paintable(None)
            self.album_art_stack.set_visible_child_name("placeholder")
        else:
            self.album_art.set_paintable(texture)
            self.album_art_stack.set_visible_child_name("art")
        return GLib.SOURCE_REMOVE

    def update(self, snapshot: SpotifySnapshot) -> None:
        waiting = not snapshot.connected
        title = snapshot.title or ("Spotify 연결 중" if waiting else "재생할 곡이 없습니다")
        self.title.set_text(title)
        self.artist_album.set_text(expanded_artist_album(snapshot))
        self._update_album_art(snapshot.art_url)
        compact_metadata = compact_spotify_metadata(snapshot, self._compact_metadata)
        self._compact_metadata = compact_metadata
        self.compact_title.set_text(compact_metadata.title or title)
        self.compact_artist.set_text(compact_metadata.artist or "아티스트 정보 없음")
        self.compact_album.set_text(compact_metadata.album or "앨범 정보 없음")
        self.play.set_icon_name("media-playback-pause-symbolic" if snapshot.playing else "media-playback-start-symbolic")
        fraction = snapshot.position_us / snapshot.length_us if snapshot.length_us > 0 else 0.0
        self.progress.set_fraction(max(0.0, min(1.0, fraction)))
        for button in (self.previous, self.play, self.next):
            button.set_sensitive(snapshot.connected and snapshot.can_control)
