from __future__ import annotations

import unittest
from pathlib import Path

from gi.repository import GLib

from luminophore_shell.spotify import SpotifySnapshot, snapshot_from_properties
from luminophore_shell.ui.spotify import (
    CompactSpotifyMetadata,
    compact_spotify_metadata,
    expanded_artist_album,
)
from luminophore_shell.ui.surface import anchored_panel_bounds


class SpotifySnapshotTests(unittest.TestCase):
    def test_parses_spotify_metadata_and_progress(self) -> None:
        snapshot = snapshot_from_properties({
            "PlaybackStatus": GLib.Variant("s", "Playing"),
            "CanControl": GLib.Variant("b", True),
            "Position": GLib.Variant("x", 30),
            "Metadata": GLib.Variant("a{sv}", {
                "xesam:title": GLib.Variant("s", "Track"),
                "xesam:artist": GLib.Variant("as", ["Artist"]),
                "xesam:album": GLib.Variant("s", "Album"),
                "mpris:artUrl": GLib.Variant("s", "https://example.test/cover.jpg"),
                "mpris:length": GLib.Variant("x", 120),
            }),
        })
        self.assertEqual(snapshot.title, "Track")
        self.assertEqual(snapshot.artist, "Artist")
        self.assertEqual(snapshot.album, "Album")
        self.assertEqual(snapshot.art_url, "https://example.test/cover.jpg")
        self.assertTrue(snapshot.playing)
        self.assertTrue(snapshot.can_control)
        self.assertEqual((snapshot.position_us, snapshot.length_us), (30, 120))

    def test_malformed_metadata_fails_closed(self) -> None:
        snapshot = snapshot_from_properties({"Metadata": "bad", "CanControl": False})
        self.assertEqual(snapshot.title, "")
        self.assertFalse(snapshot.can_control)

    def test_snapshot_default_is_disconnected(self) -> None:
        self.assertFalse(SpotifySnapshot().connected)

    def test_luminophore_shell_does_not_launch_spotify(self) -> None:
        app_source = Path("luminophore_shell/app.py").read_text(encoding="utf-8")
        provider_source = Path("luminophore_shell/spotify.py").read_text(encoding="utf-8")
        self.assertNotIn("ensure_running", app_source)
        self.assertNotIn("DesktopAppInfo", provider_source)

    def test_compact_uses_current_track_metadata(self) -> None:
        metadata = compact_spotify_metadata(
            SpotifySnapshot(
                connected=True,
                playback_status="Playing",
                title="Track",
                artist="Artist",
                album="Album",
            )
        )

        self.assertEqual(metadata, CompactSpotifyMetadata("Track", "Artist", "Album"))

    def test_compact_keeps_last_track_during_transient_disconnect(self) -> None:
        previous = CompactSpotifyMetadata("Previous", "Artist", "Album")

        metadata = compact_spotify_metadata(
            SpotifySnapshot(error="spotify_connecting"),
            previous,
        )

        self.assertEqual(metadata, previous)

    def test_compact_metadata_is_independent_of_playback_status(self) -> None:
        metadata = compact_spotify_metadata(
            SpotifySnapshot(
                connected=True,
                playback_status="Paused",
                title="Paused Track",
                artist="Artist",
                album="Album",
            )
        )

        self.assertEqual(metadata.title, "Paused Track")

    def test_compact_view_reuses_weather_typography_and_luminophore_palette(self) -> None:
        source = Path("luminophore_shell/ui/spotify.py").read_text(encoding="utf-8")
        self.assertIn('self.compact_title.add_css_class("weather-clock")', source)
        self.assertIn('self.compact_title.add_css_class("luminophore-key-primary")', source)
        self.assertEqual(source.count('add_css_class("weather-temperature")'), 2)
        self.assertIn('self.compact_artist.add_css_class("luminophore-key-secondary")', source)
        self.assertIn('self.compact_album.add_css_class("luminophore-key-secondary")', source)
        self.assertNotIn("compact_status", source)

    def test_expanded_artist_album_uses_requested_separator(self) -> None:
        self.assertEqual(
            expanded_artist_album(SpotifySnapshot(artist="Artist", album="Album")),
            "Artist - Album",
        )

    def test_expanded_view_uses_luminophore_hierarchy_and_async_album_art(self) -> None:
        source = Path("luminophore_shell/ui/spotify.py").read_text(encoding="utf-8")
        self.assertIn('self.title.add_css_class("luminophore-key-primary")', source)
        self.assertIn('self.artist_album.add_css_class("luminophore-key-secondary")', source)
        self.assertIn('self.progress.add_css_class("spotify-progress")', source)
        self.assertIn('self.expanded.append(self.album_art_stack)', source)
        self.assertIn('scheme not in {"https", "file"}', source)
        self.assertIn("self._MAX_ART_BYTES + 1", source)
        self.assertIn("GLib.idle_add(self._apply_album_art", source)
        self.assertIn("pixbuf.scale_simple(", source)
        self.assertIn("self._ART_SIZE", source)
        self.assertIn("request != self._art_request", source)


class BottomPlacementTests(unittest.TestCase):
    def test_bottom_left_uses_bottom_inset(self) -> None:
        self.assertEqual(
            anchored_panel_bounds("left", 1920, 320, 96, 24, 0, 1080, 24, "bottom"),
            (24, 960, 320, 96),
        )

    def test_existing_top_contract_is_unchanged(self) -> None:
        self.assertEqual(anchored_panel_bounds("right", 1920, 224, 32, 24, 24), (1672, 24, 224, 32))


if __name__ == "__main__":
    unittest.main()
