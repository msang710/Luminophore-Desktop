from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image

from luminophore_shell.hyprland import MonitorRecord, WorkspaceRef
from luminophore_shell.theme import Palette
from luminophore_shell.wallpaper_backends import (
    MAX_ANIMATION_FRAMES,
    WallpaperBackendError,
    WallpaperSnapshot,
    WallpaperSource,
    _frame_indices,
    discover_wallpaper_snapshot,
    extract_snapshot_palettes,
    parse_awww_query,
    parse_hyprpaper_active,
)


WORKSPACE = WorkspaceRef(1, "1")
LEFT = MonitorRecord(0, "DP-2", 0, 0, 100, 100, WORKSPACE)
RIGHT = MonitorRecord(1, "DP-1", 100, 0, 100, 100, WORKSPACE)
FALLBACK = Palette("#010101", "#020202")


class WallpaperBackendsTests(unittest.TestCase):
    def test_hyprpaper_parser_preserves_distinct_output_paths(self) -> None:
        snapshot = parse_hyprpaper_active(
            "DP-2: /home/user/사진/yangyang.jpg\nDP-1: /home/user/Pictures/with space.jpg\n"
        )

        self.assertEqual(snapshot.provider, "hyprpaper")
        self.assertEqual(snapshot.sources["DP-2"].value, "/home/user/사진/yangyang.jpg")
        self.assertEqual(snapshot.sources["DP-1"].value, "/home/user/Pictures/with space.jpg")

    def test_awww_parser_supports_images_colors_and_rejects_namespace_conflicts(self) -> None:
        payload = {
            "awww-daemon": [
                {"name": "DP-2", "displaying": {"image": "/wall/left.gif"}},
                {"name": "DP-1", "displaying": {"color": "12ab34ff"}},
            ]
        }
        snapshot = parse_awww_query(json.dumps(payload))

        self.assertEqual(snapshot.sources["DP-2"], WallpaperSource("image", "/wall/left.gif"))
        self.assertEqual(snapshot.sources["DP-1"], WallpaperSource("color", "#12AB34"))

        payload["other"] = [{"name": "DP-2", "displaying": {"image": "/wall/other.gif"}}]
        with self.assertRaisesRegex(WallpaperBackendError, "namespace"):
            parse_awww_query(payload)

    def test_preferred_provider_disambiguates_without_querying_other_provider(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, _timeout: float) -> subprocess.CompletedProcess[str]:
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "DP-2: /left.jpg\nDP-1: /right.jpg\n", "")

        snapshot = discover_wallpaper_snapshot([LEFT, RIGHT], "hyprpaper", runner=runner)

        self.assertEqual(snapshot.provider, "hyprpaper")
        self.assertEqual(len(calls), 1)

    def test_multiple_active_providers_without_hint_are_rejected(self) -> None:
        def runner(argv, _timeout: float) -> subprocess.CompletedProcess[str]:
            if "hyprpaper" in argv:
                stdout = "DP-2: /left.jpg\nDP-1: /right.jpg\n"
            else:
                stdout = json.dumps({
                    "awww-daemon": [
                        {"name": "DP-2", "displaying": {"image": "/left.gif"}},
                        {"name": "DP-1", "displaying": {"image": "/right.gif"}},
                    ]
                })
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        with self.assertRaisesRegex(WallpaperBackendError, "여러 개"):
            discover_wallpaper_snapshot([LEFT, RIGHT], runner=runner)

    def test_static_images_are_extracted_per_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            left = Path(directory) / "left.png"
            right = Path(directory) / "right.png"
            Image.new("RGB", (100, 100), "#A05010").save(left)
            Image.new("RGB", (100, 100), "#104FA0").save(right)
            snapshot = WallpaperSnapshot(
                "hyprpaper",
                {
                    "DP-2": WallpaperSource("image", str(left)),
                    "DP-1": WallpaperSource("image", str(right)),
                },
            )

            palettes = extract_snapshot_palettes(snapshot, [LEFT, RIGHT], FALLBACK)

        self.assertEqual(set(palettes), {"DP-2", "DP-1"})
        self.assertNotEqual(palettes["DP-2"].primary, palettes["DP-1"].primary)

    def test_animated_image_uses_bounded_representative_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "animated.gif"
            frames = [Image.new("RGB", (40, 40), "#A05010")]
            frames.extend(Image.new("RGB", (40, 40), "#104FA0") for _index in range(19))
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=20, loop=0)
            snapshot = WallpaperSnapshot(
                "awww",
                {"DP-2": WallpaperSource("image", str(path))},
            )

            palette = extract_snapshot_palettes(snapshot, [LEFT], FALLBACK)["DP-2"]

        self.assertNotEqual(palette, FALLBACK)
        self.assertLessEqual(len(_frame_indices(20)), MAX_ANIMATION_FRAMES)
        self.assertEqual(_frame_indices(20)[-1], 19)

    def test_awww_solid_color_uses_common_palette_algorithm(self) -> None:
        snapshot = WallpaperSnapshot("awww", {"DP-2": WallpaperSource("color", "#B04020")})

        palette = extract_snapshot_palettes(snapshot, [LEFT], FALLBACK)["DP-2"]

        self.assertNotEqual(palette, FALLBACK)
        self.assertNotEqual(palette.primary, palette.secondary)

    def test_missing_output_or_relative_path_never_returns_partial_palette(self) -> None:
        missing = WallpaperSnapshot("hyprpaper", {"DP-2": WallpaperSource("image", "/left.png")})
        relative = WallpaperSnapshot(
            "hyprpaper",
            {
                "DP-2": WallpaperSource("image", "left.png"),
                "DP-1": WallpaperSource("image", "right.png"),
            },
        )

        with self.assertRaisesRegex(WallpaperBackendError, "일부 모니터"):
            extract_snapshot_palettes(missing, [LEFT, RIGHT], FALLBACK)
        with self.assertRaisesRegex(WallpaperBackendError, "읽을 수 없습니다"):
            extract_snapshot_palettes(relative, [LEFT, RIGHT], FALLBACK)


if __name__ == "__main__":
    unittest.main()
