from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from luminophore_shell.hyprland import MonitorRecord, WorkspaceRef
from luminophore_shell.palette_controller import PaletteExtractionController, PaletteSettingsState
from luminophore_shell.theme import Palette
from luminophore_shell.matugen import generated_palette, parse_matugen_json
from tests.test_matugen import fixture as matugen_fixture
from luminophore_shell.wallpaper_backends import WallpaperSnapshot, WallpaperSource


MONITOR = MonitorRecord(0, "DP-1", 0, 0, 100, 100, WorkspaceRef(1, "1"))


class PaletteControllerTests(unittest.TestCase):
    def _controller(self, path: Path, changes: list[PaletteSettingsState]) -> PaletteExtractionController:
        return PaletteExtractionController(
            path,
            lambda: [MONITOR],
            lambda: Palette("#111111", "#222222"),
            changes.append,
            lambda: None,
            provider="hyprpaper",
        )

    def test_duplicate_extract_is_rejected_while_worker_runs(self) -> None:
        changes: list[PaletteSettingsState] = []
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(Path(directory) / "config.toml", changes)
            def wait_then_palette(*_args) -> dict[str, Palette]:
                gate.wait(1)
                return {"DP-1": Palette("#123456", "#ABCDEF")}

            with (
                patch("luminophore_shell.palette_controller.discover_wallpaper_snapshot", return_value=WallpaperSnapshot(
                    "hyprpaper", {"DP-1": WallpaperSource("image", "/unused.png")},
                )),
                patch("luminophore_shell.palette_controller.extract_snapshot_palettes", side_effect=wait_then_palette),
            ):
                self.assertTrue(controller.extract())
                self.assertFalse(controller.extract())
                gate.set()
                controller.shutdown()

    def test_set_provider_accepts_only_static_providers_while_idle(self) -> None:
        changes: list[PaletteSettingsState] = []
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(Path(directory) / "config.toml", changes)
            self.assertTrue(controller.set_provider("awww"))
            self.assertEqual(controller._provider, "awww")
            self.assertFalse(controller.set_provider(""))
            self.assertFalse(controller.set_provider("unknown"))
            self.assertEqual(controller._provider, "awww")
            controller.shutdown()

    def test_set_provider_is_rejected_while_extraction_is_busy(self) -> None:
        changes: list[PaletteSettingsState] = []
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(Path(directory) / "config.toml", changes)

            def wait_then_palette(*_args) -> dict[str, Palette]:
                gate.wait(1)
                return {"DP-1": Palette("#123456", "#ABCDEF")}

            with (
                patch("luminophore_shell.palette_controller.discover_wallpaper_snapshot", return_value=WallpaperSnapshot(
                    "hyprpaper", {"DP-1": WallpaperSource("image", "/unused.png")},
                )),
                patch("luminophore_shell.palette_controller.extract_snapshot_palettes", side_effect=wait_then_palette),
            ):
                self.assertTrue(controller.extract())
                self.assertFalse(controller.set_provider("awww"))
                self.assertEqual(controller._provider, "hyprpaper")
                gate.set()
                controller.shutdown()

    def test_no_monitor_worker_reports_stable_error_category(self) -> None:
        changes: list[PaletteSettingsState] = []
        with tempfile.TemporaryDirectory() as directory:
            controller = PaletteExtractionController(
                Path(directory) / "config.toml",
                lambda: [],
                lambda: Palette("#111111", "#222222"),
                changes.append,
                lambda: None,
                provider="hyprpaper",
            )
            self.assertTrue(controller.extract())
            assert controller._worker is not None
            controller._worker.join(1)
            self.assertEqual(controller.state.phase, "error")
            self.assertEqual(controller.state.error_category, "no_monitors")
            controller.shutdown()

    def test_apply_writes_state_before_switching_source(self) -> None:
        changes: list[PaletteSettingsState] = []
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(Path(directory) / "config.toml", changes)
            controller._state = PaletteSettingsState(
                "preview",
                preview={"DP-1": Palette("#123456", "#ABCDEF")},
                captured_at=10.0,
                provider="hyprpaper",
            )
            with (
                patch("luminophore_shell.palette_controller.write_palette_state", side_effect=lambda *_args: calls.append("state")),
                patch("luminophore_shell.palette_controller.write_theme_source", side_effect=lambda *_args: calls.append("config")),
            ):
                self.assertTrue(controller.apply())

        self.assertEqual(calls, ["state", "config"])

    def test_static_provider_is_preserved_from_preview_to_apply(self) -> None:
        changes: list[PaletteSettingsState] = []
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(Path(directory) / "config.toml", changes)
            snapshot = WallpaperSnapshot("hyprpaper", {"DP-1": WallpaperSource("image", "/unused.png")})
            with (
                patch("luminophore_shell.palette_controller.discover_wallpaper_snapshot", return_value=snapshot),
                patch(
                    "luminophore_shell.palette_controller.extract_snapshot_palettes",
                    return_value={"DP-1": Palette("#123456", "#ABCDEF")},
                ),
            ):
                self.assertTrue(controller.extract())
                assert controller._worker is not None
                controller._worker.join(1)

            self.assertEqual(controller.state.phase, "preview")
            self.assertEqual(controller.state.provider, "hyprpaper")

    def test_extract_never_auto_applies(self) -> None:
        changes: list[PaletteSettingsState] = []
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(Path(directory) / "config.toml", changes)
            snapshot = WallpaperSnapshot("hyprpaper", {"DP-1": WallpaperSource("image", "/unused.png")})
            with (
                patch("luminophore_shell.palette_controller.discover_wallpaper_snapshot", return_value=snapshot),
                patch("luminophore_shell.palette_controller.extract_snapshot_palettes", return_value={"DP-1": Palette("#123456", "#ABCDEF")}),
                patch("luminophore_shell.palette_controller.write_palette_state") as write_state,
                patch("luminophore_shell.palette_controller.write_theme_source") as write_source,
            ):
                self.assertTrue(controller.extract())
                assert controller._worker is not None
                controller._worker.join(1)
            self.assertEqual(controller.state.phase, "preview")
            write_state.assert_not_called()
            write_source.assert_not_called()

    def test_matugen_scheme_is_kept_for_separate_system_apply_and_v2_state(self) -> None:
        changes: list[PaletteSettingsState] = []
        generated = generated_palette(parse_matugen_json(matugen_fixture()))
        with tempfile.TemporaryDirectory() as directory:
            controller = self._controller(Path(directory) / "config.toml", changes)
            snapshot = WallpaperSnapshot("hyprpaper", {"DP-1": WallpaperSource("image", "/unused.png")})
            with (
                patch("luminophore_shell.palette_controller.discover_wallpaper_snapshot", return_value=snapshot),
                patch("luminophore_shell.palette_controller.extract_snapshot_palettes", return_value={"DP-1": generated}),
            ):
                self.assertTrue(controller.extract())
                assert controller._worker is not None
                controller._worker.join(1)

            self.assertEqual(controller.state.schemes["DP-1"].generation_id, generated.scheme.generation_id)
            with (
                patch("luminophore_shell.palette_controller.write_palette_state") as write_state,
                patch("luminophore_shell.palette_controller.write_theme_source"),
            ):
                self.assertTrue(controller.apply())

            self.assertEqual(write_state.call_args.kwargs["schemes"]["DP-1"], generated.scheme)
            self.assertEqual(write_state.call_args.kwargs["provider"], "hyprpaper")


if __name__ == "__main__":
    unittest.main()
