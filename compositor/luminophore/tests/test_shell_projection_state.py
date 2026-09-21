import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class LuminophoreShellProjectionStateTests(unittest.TestCase):
    def test_protocol_v4_carries_role_geometry_and_reveal_state(self) -> None:
        bindings = (ROOT / "src/config/lua/bindings/LuaBindingsDispatchers.cpp").read_text()
        fixture = json.loads((ROOT / "luminophore/tests/fixtures/shell-projection-v4.json").read_text())

        self.assertEqual(fixture["version"], 4)
        self.assertEqual(fixture["requested_plane"], "overlay")
        self.assertEqual(fixture["role"], "launcher-drop")
        for field in ("revision", "content_revision", "requested_plane", "role", "reveal_from", "reveal_to", "panel_width", "panel_height"):
            self.assertIn(f'"{field}"', bindings)
        self.assertIn("version != 4", bindings)
        self.assertIn("revision must be a positive integer", bindings)

    def test_snapshot_is_immutable_and_committed_at_frame_boundary(self) -> None:
        header = (ROOT / "src/luminophore/LuminophoreShellProjection.hpp").read_text()
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        transaction = (ROOT / "src/luminophore/LuminophoreMonitorTransaction.cpp").read_text()

        self.assertIn("std::shared_ptr<const SSnapshot>", header)
        self.assertIn("requestedPlane", header)
        self.assertIn("m_framePlanes", header)
        self.assertIn("contentRevision", header)
        self.assertIn("m_pending[surfaceNamespace] = IT->second", controller)
        self.assertIn("m_committed[NAMESPACE]", controller)
        self.assertIn("shellProjection()->applyPendingForMonitor(monitor)", transaction)
        self.assertLess(renderer.index("Luminophore::monitorTransaction()->beginFrame(pMonitor)"), renderer.index("canAttemptDirectScanoutFast"))

    def test_state_transition_does_not_mutate_layer_or_input_topology(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()

        for forbidden in (
            "m_layerSurfaceLayers[sourceLayer]",
            "SURFACE->m_layer = TARGET",
            "m_aboveFullscreen",
            "LS_ALPHA_FADE",
            "arrangeLayersForMonitor",
            "m_scheduledRecalc",
            "simulateMouseMovement",
        ):
            self.assertNotIn(forbidden, controller)

    def test_stale_duplicate_and_changed_bindings_fail_closed(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()

        self.assertIn("revision < latest->second", controller)
        self.assertIn("revision == latest->second", controller)
        self.assertIn("sameRequest(m_prepared)", controller)
        self.assertIn("sameRequest(m_pending)", controller)
        self.assertIn("sameRequest(m_committed)", controller)
        self.assertIn("surface->m_mapped", controller)
        self.assertIn("surface->m_namespace == snapshot->surfaceNamespace", controller)
        self.assertIn("surface->m_monitor.lock() == monitor", controller)
        self.assertIn("std::chrono::milliseconds(500)", controller)
        self.assertIn("std::chrono::seconds(1)", controller)

    def test_bloom_consumption_uses_committed_snapshot(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        method = controller[controller.index("void CLuminophoreShellProjection::enqueueBloom") : controller.index("auto CLuminophoreShellProjection::makeSnapshot")]

        self.assertNotIn("effectivePlane != SHELL_PROJECTION_OVERLAY", method)
        self.assertIn("snapshotFor", method)
        self.assertIn("m_bloom->enqueue", method)
        self.assertIn("snapshot->renderBox", method)

    def test_live_panel_geometry_is_sampled_once_per_projection_frame(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()

        self.assertIn("CBox CLuminophoreShellProjection::panelFor", controller)
        self.assertIn("effectiveInputRegion().getExtents()", controller)
        self.assertIn(".panel   = panelFor", controller)
        self.assertIn(".renderBox        = RENDER_BOX", controller)

    def test_forced_panel_blur_survives_an_opaque_initial_texture(self) -> None:
        renderer = (ROOT / "src/render/ElementRenderer.cpp").read_text()
        opengl = (ROOT / "src/render/OpenGL.cpp").read_text()

        self.assertIn(
            "m_data.forceBlurRegion || !TEXTURE->m_opaque || ALPHA < 1.F || OVERALL_ALPHA < 1.F",
            renderer,
        )
        self.assertIn(".forceBlurBlend        = m_data.forceBlurRegion", renderer)
        self.assertIn("if (!*PBLEND && !data.forceBlurBlend)", opengl)
        self.assertIn(".blur           = *PBLEND || data.forceBlurBlend", opengl)

    def test_explicit_osd_panel_precedes_live_corner_input_region(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        panel_for = controller[
            controller.index("CBox CLuminophoreShellProjection::panelFor") :
            controller.index("void CLuminophoreShellProjection::damageSnapshot")
        ]

        explicit = panel_for.index("snapshot->style.panel.width > 0.F")
        live = panel_for.index("effectiveInputRegion().getExtents()")
        fallback = panel_for.index("SURFACE->m_geometry.width")
        self.assertLess(explicit, live)
        self.assertLess(live, fallback)
        self.assertIn("m_framePanels.erase(item.first)", controller)

    def test_plane_transition_damages_projected_surface_regions(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        resolve = controller[
            controller.index("void CLuminophoreShellProjection::resolveFrameForMonitor") :
            controller.index("void CLuminophoreShellProjection::presentedForMonitor")
        ]

        self.assertIn("PLANE_CHANGED", resolve)
        self.assertIn("OLD_PLANE->second != FRAME_PLANE", resolve)
        self.assertIn("damagePanel(snapshot, old->second)", resolve)
        self.assertIn("damagePanel(snapshot, panel)", resolve)

    def test_presentation_and_scanout_policy_are_owned_by_p1_04(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()

        self.assertIn("m_awaitingPresentation", controller)
        self.assertIn("luminophoreshellpresented", controller)
        self.assertIn("blocksDirectScanout", controller)

    def test_canonical_frame_snapshot_is_immutable_and_complete(self) -> None:
        header = (ROOT / "src/luminophore/LuminophoreShellProjection.hpp").read_text()
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()

        self.assertIn("struct SShellSurfaceFrameSnapshot", header)
        self.assertIn("std::shared_ptr<const SShellSurfaceFrameSnapshot>", header)
        for field in ("contentBox", "renderBox", "blurBox", "bloomBox", "hitBox"):
            self.assertIn(field, header)
        for api in ("snapshotFor", "renderList"):
            self.assertIn(api, header)
            self.assertIn(f"CLuminophoreShellProjection::{api}", controller)
        self.assertIn("m_frameSnapshots[name] = frameSnapshot", controller)
        self.assertIn("SHELL_SNAPSHOT_COMMITTED", controller)
        self.assertIn("SHELL_SNAPSHOT_PRESENTED", controller)

    def test_presented_state_does_not_depend_on_ipc_telemetry(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        presented = controller[
            controller.index("void CLuminophoreShellProjection::presentedForMonitor") :
            controller.index("bool CLuminophoreShellProjection::blocksDirectScanout")
        ]

        state_update = presented.index("SHELL_SNAPSHOT_PRESENTED")
        telemetry_guard = presented.index("if (!g_pEventManager)")
        self.assertLess(state_update, telemetry_guard)

    def test_content_revision_does_not_discard_bloom_cache(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        apply_pending = controller[
            controller.index("void CLuminophoreShellProjection::applyPendingForMonitor") :
            controller.index("void CLuminophoreShellProjection::resolveFrameForMonitor")
        ]

        self.assertIn("m_committed[NAMESPACE]", apply_pending)
        self.assertNotIn("m_bloom->discard", apply_pending)
        self.assertNotIn("contentRevision !=", apply_pending)


if __name__ == "__main__":
    unittest.main()
