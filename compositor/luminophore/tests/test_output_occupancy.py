from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class LuminophoreOutputOccupancyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = (ROOT / "src/managers/fullscreen/handler/FullscreenHandler.cpp").read_text()
        self.target = (ROOT / "src/layout/target/WindowTarget.cpp").read_text()
        self.renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        self.input = (ROOT / "src/managers/input/InputManager.cpp").read_text()
        self.occupy = (ROOT / "src/luminophore/LuminophoreOccupyOutputController.cpp").read_text()

    def test_both_protocol_modes_use_one_monitor_geometry(self) -> None:
        start = self.handler.index("void IFullscreenHandler::setTargetSizeAndPosition")
        end = self.handler.index("void IFullscreenHandler::syncTargetSizeAndPosition", start)
        setter = self.handler[start:end]
        self.assertIn("MONITOR->logicalBox()", setter)
        self.assertNotIn("FSMODE_MAXIMIZED", setter)
        self.assertNotIn("workArea", setter)

        start = self.handler.index("void IFullscreenHandler::syncTargetSizeAndPosition")
        end = self.handler.index("void IFullscreenHandler::setNoMembersAboveFullscreen", start)
        sync = self.handler[start:end]
        self.assertIn("MONITOR->logicalBox()", sync)
        self.assertNotIn("FSMODE_MAXIMIZED", sync)
        self.assertNotIn("workArea", sync)

    def test_target_application_does_not_reintroduce_mode_specific_geometry(self) -> None:
        start = self.target.index("/* FS Handling */")
        end = self.target.index("/* Non-Fs Tiled Windows */", start)
        fullscreen = self.target[start:end]
        self.assertNotIn("FSMODE_FULLSCREEN", fullscreen)
        self.assertNotIn("FSMODE_MAXIMIZED", fullscreen)
        self.assertNotIn("getFullWindowReservedArea", fullscreen)

    def test_protocol_mode_only_controls_border_and_rounding(self) -> None:
        self.assertIn("internal == Fullscreen::FSMODE_FULLSCREEN", self.renderer)
        self.assertIn("internal != Fullscreen::FSMODE_FULLSCREEN", self.renderer)
        self.assertIn("renderdata.dontRound", self.renderer)
        self.assertIn("renderdata.decorate", self.renderer)

    def test_occupy_action_accepts_either_existing_occupied_mode(self) -> None:
        self.assertIn("Fullscreen::controller()->isFullscreen(window)", self.occupy)
        self.assertNotIn("CURRENT.internal == Fullscreen::FSMODE_FULLSCREEN", self.occupy)

    def test_shell_projection_precedes_common_occupied_window_hit_test(self) -> None:
        projected = self.input.index("projectedOverlaySurfaces(PMONITOR)")
        occupied = self.input.index("Fullscreen::controller()->hasFullscreen(PWORKSPACE)", projected)
        self.assertLess(projected, occupied)
        self.assertNotIn("internal == Fullscreen::FSMODE_MAXIMIZED", self.input)


if __name__ == "__main__":
    unittest.main()
