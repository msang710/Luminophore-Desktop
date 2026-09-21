import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class LuminophoreShellProjectionRenderPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        self.projection = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        self.surface_element = (ROOT / "src/render/pass/SurfacePassElement.cpp").read_text()
        self.element_renderer = (ROOT / "src/render/ElementRenderer.cpp").read_text()

    def test_no_workspace_keeps_physical_layer_order(self) -> None:
        start = self.renderer.index("if UNLIKELY (!pWorkspace)")
        end = self.renderer.index("if LIKELY (!*PXPMODE)", start)
        no_workspace = self.renderer[start:end]

        self.assertNotIn("isProjectedOverlay", no_workspace)
        self.assertNotIn("renderLuminophoreShellPlane", no_workspace)

    def test_projected_bottom_content_is_skipped_once(self) -> None:
        start = self.renderer.index("if LIKELY (!*PXPMODE)")
        end = self.renderer.index("// pre window pass", start)
        physical_bottom = self.renderer[start:end]

        self.assertIn("snapshotFor(SURFACE, pMonitor)", physical_bottom)
        self.assertIn("continue;", physical_bottom)
        self.assertIn("SNAPSHOT->plane", physical_bottom)
        self.assertLess(physical_bottom.index("enqueueBloom"), physical_bottom.index("renderLayer(SURFACE"))

    def test_effective_plane_sits_between_top_and_overlay(self) -> None:
        post_windows = self.renderer.index("RENDER_POST_WINDOWS")
        top = self.renderer.index("ZWLR_LAYER_SHELL_V1_LAYER_TOP", post_windows)
        projected = self.renderer.index("renderLuminophoreShellPlane(pMonitor, time)", top)
        overlay = self.renderer.index("ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY", projected)

        self.assertLess(top, projected)
        self.assertLess(projected, overlay)

    def test_bloom_and_content_are_enqueued_from_one_projection_slot(self) -> None:
        start = self.renderer.index("void IHyprRenderer::renderLuminophoreShellPlane")
        end = self.renderer.index("void IHyprRenderer::renderIME", start)
        slot = self.renderer[start:end]

        self.assertIn('TRACY_GPU_ZONE("RenderLuminophoreShellPlane")', slot)
        self.assertLess(slot.index("enqueueBloom"), slot.index("renderLayer"))
        generic_layer = self.renderer[self.renderer.index("void IHyprRenderer::renderLayer") : self.renderer.index("void IHyprRenderer::renderAllClientsForWorkspace")]
        self.assertNotIn("enqueueBloom", generic_layer)

    def test_bloom_is_valid_on_both_effective_planes(self) -> None:
        method = self.projection[
            self.projection.index("void CLuminophoreShellProjection::enqueueBloom") :
            self.projection.index("bool CLuminophoreShellProjection::isProjected", self.projection.index("void CLuminophoreShellProjection::enqueueBloom"))
        ]
        self.assertNotIn("effectivePlane != SHELL_PROJECTION_OVERLAY", method)
        self.assertIn("snapshotFor", method)

    def test_projected_shell_blur_uses_its_panel_not_initial_opaque_metadata(self) -> None:
        generic_layer = self.renderer[
            self.renderer.index("void IHyprRenderer::renderLayer") :
            self.renderer.index("void IHyprRenderer::renderAllClientsForWorkspace")
        ]
        self.assertRegex(generic_layer, r"renderdata\.forceBlurRegion\s*= true")
        self.assertRegex(generic_layer, r"renderdata\.blurRegion\s*= SNAPSHOT->blurBox")
        self.assertIn("m_data.forceBlurRegion ||", self.surface_element)
        self.assertIn(".forceBlurRegion       = m_data.mainSurface && m_data.forceBlurRegion", self.element_renderer)
        self.assertIn("if (element->m_data.forceBlurRegion)", self.element_renderer)
        self.assertIn("inverseOpaque = element->m_data.blurRegion", self.element_renderer)

    def test_semantic_blur_uses_frame_bundle_and_bypasses_global_cache(self) -> None:
        layer = self.renderer[
            self.renderer.index("void IHyprRenderer::renderLayer") :
            self.renderer.index("void IHyprRenderer::renderAllClientsForWorkspace")
        ]
        self.assertIn("SNAPSHOT->visualBundle.blurEnabled", layer)
        self.assertIn("SNAPSHOT->visualBundle.blurParameters()", layer)
        self.assertRegex(layer, r"renderdata\.blockBlurOptimization\s*= true")
        self.assertRegex(self.element_renderer, r"\.blurParameters\s*= m_data.blurParameters")
        self.assertIn("element->m_data.blurParameters.has_value()", self.element_renderer)
        self.assertIn("blurMainFramebuffer(element->m_data.a, &inverseOpaque, element->m_data.blurParameters)", self.element_renderer)
        gl = (ROOT / "src/render/OpenGL.cpp").read_text()
        self.assertIn("parameters ? parameters->passes : *PBLURPASSES", gl)
        self.assertIn("parameters ? parameters->size : *PBLURSIZE", gl)
        self.assertIn("damage.expand(BLUR_SIZE * pow(2, BLUR_PASSES))", gl)
        self.assertIn("SHADER_RADIUS, BLUR_SIZE * a", gl)

    def test_projection_never_rewrites_physical_layer_metadata(self) -> None:
        for forbidden in (
            "m_layerSurfaceLayers[sourceLayer]",
            "SURFACE->m_layer = TARGET",
            "m_aboveFullscreen",
            "arrangeLayersForMonitor",
            "simulateMouseMovement",
        ):
            self.assertNotIn(forbidden, self.projection)

    def test_only_bound_bottom_surfaces_can_be_projected(self) -> None:
        self.assertIn("SURFACE->m_layer == ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM", self.projection)
        self.assertIn("effectivePlaneFor", self.projection)
        self.assertIn("m_layerSurfaceLayers[ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM]", self.projection)

    def test_bloom_uses_canonical_render_geometry_and_monitor_transform(self) -> None:
        bloom = (ROOT / "src/render/luminophore/LuminophoreShellBloom.cpp").read_text()
        projection = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()

        self.assertNotIn("effectiveInputRegion().getExtents()", bloom)
        self.assertIn("globalPanel.copy().translate(-monitor->m_position)", bloom)
        self.assertIn("panel.scale(monitor->m_scale).round()", bloom)
        self.assertIn("renderModif.applyToBox", bloom)
        self.assertIn(".panel   = panelFor", projection)
        self.assertIn("snapshot->renderBox", projection)

    def test_bloom_uses_reference_four_level_pyramid(self) -> None:
        bloom = (ROOT / "src/render/luminophore/LuminophoreShellBloom.cpp").read_text()
        pyramid = (ROOT / "src/render/luminophore/LuminophoreBloomPyramid.hpp").read_text()

        self.assertIn("LUMINOPHORE_BLOOM_LEVEL_COUNT 4", pyramid)
        self.assertIn("13-tap", pyramid)
        self.assertIn("9-tap", pyramid)
        self.assertIn("luminophore_bloom_prepare_capacity", bloom)
        self.assertIn("luminophore_bloom_generate", bloom)


if __name__ == "__main__":
    unittest.main()
