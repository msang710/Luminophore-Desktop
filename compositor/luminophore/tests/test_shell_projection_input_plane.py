import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class LuminophoreShellProjectionInputPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.input_manager = (ROOT / "src/managers/input/InputManager.cpp").read_text()
        self.projection = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        self.hit_tester = (ROOT / "src/desktop/state/ViewHitTester.cpp").read_text()

    def test_effective_input_plane_matches_render_order(self) -> None:
        start = self.input_manager.index("// overlays are above fullscreen")
        end = self.input_manager.index("// then, we check if the workspace", start)
        hit_order = self.input_manager[start:end]

        overlay = hit_order.index("ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY")
        projected = hit_order.index("projectedOverlaySurfaces")
        top = hit_order.index("ZWLR_LAYER_SHELL_V1_LAYER_TOP")
        self.assertLess(overlay, projected)
        self.assertLess(projected, top)

    def test_projected_surface_is_not_hit_again_as_physical_bottom(self) -> None:
        self.assertEqual(self.input_manager.count("physicalBottomSurfaces(PMONITOR)"), 2)
        self.assertIn("!isProjectedOverlay(WEAK.lock(), monitor)", self.projection)

    def test_fullscreen_accepts_effective_overlay_despite_bottom_metadata(self) -> None:
        start = self.input_manager.index("if (HAS_OUTPUT_OCCUPANCY)")
        end = self.input_manager.index("// then windows", start)
        fullscreen = self.input_manager[start:end]

        self.assertIn("isProjectedOverlay(pFoundLayerSurface, PMONITOR)", fullscreen)
        self.assertIn("pFoundLayerSurface && !IS_LUMINOPHORE_PROJECTED", fullscreen)

    def test_refocus_uses_the_same_effective_order(self) -> None:
        start = self.input_manager.index("bool CInputManager::refocusLastWindow")
        end = self.input_manager.index("bool CInputManager::isConstrained", start)
        refocus = self.input_manager[start:end]

        overlay = refocus.index("ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY")
        projected = refocus.index("projectedOverlaySurfaces")
        top = refocus.index("ZWLR_LAYER_SHELL_V1_LAYER_TOP")
        self.assertLess(overlay, projected)
        self.assertLess(projected, top)

    def test_existing_surface_input_regions_remain_authoritative(self) -> None:
        self.assertIn("!surf->m_current.inputIsInfinite", self.hit_tester)
        self.assertIn("surf->m_current.input.empty()", self.hit_tester)
        self.assertIn("const std::vector<PHLLSREF>* layerSurfaces", self.hit_tester)


if __name__ == "__main__":
    unittest.main()
