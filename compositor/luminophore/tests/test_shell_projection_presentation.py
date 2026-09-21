import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class LuminophoreShellProjectionPresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projection = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        self.header = (ROOT / "src/luminophore/LuminophoreShellProjection.hpp").read_text()
        self.monitor = (ROOT / "src/output/Monitor.cpp").read_text()
        self.renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        self.transaction = (ROOT / "src/luminophore/LuminophoreMonitorTransaction.cpp").read_text()

    def test_generation_is_swapped_at_frame_start(self) -> None:
        begin = self.renderer.index("Luminophore::monitorTransaction()->beginFrame(pMonitor)")
        scanout = self.renderer.index("canAttemptDirectScanoutFast", begin)
        self.assertLess(begin, scanout)
        self.assertIn("shellProjection()->applyPendingForMonitor(monitor)", self.transaction)
        self.assertIn("m_awaitingPresentation[NAMESPACE] = SNAPSHOT", self.projection)

    def test_ack_is_emitted_only_from_output_presentation(self) -> None:
        monitor_ack = self.monitor.index("Luminophore::monitorTransaction()->presented(m_self.lock())")
        output_presented = self.monitor.index("m_events.presented.emit", monitor_ack)
        self.assertLess(monitor_ack, output_presented)
        self.assertIn("shellProjection()->presentedForMonitor(monitor)", self.transaction)
        self.assertIn('.event = "luminophoreshellpresented"', self.projection)
        self.assertIn("m_awaitingPresentation.erase", self.projection)

    def test_projection_damage_is_bounded_to_content_and_bloom(self) -> None:
        self.assertIn("effectiveInputRegion().getExtents()", self.projection)
        self.assertIn("damageBox(DAMAGE.expand(PADDING).round())", self.projection)
        commit = self.projection[self.projection.index("m_pending[surfaceNamespace] = IT->second") : self.projection.index("return true;", self.projection.index("m_pending[surfaceNamespace] = IT->second"))]
        self.assertNotIn("damageMonitor", commit)

    def test_visible_projection_blocks_direct_scanout_only_on_its_monitor(self) -> None:
        self.assertIn("SNAPSHOT->monitor.lock() == monitor", self.projection)
        self.assertIn("SHELL_PROJECTION_OVERLAY", self.projection)
        self.assertIn("compositionPolicy()->blocksDirectScanout", self.monitor)

    def test_bloom_animation_uses_reference_cadence_and_bounded_damage(self) -> None:
        self.assertIn("std::chrono::microseconds(13889)", self.projection)
        self.assertIn("SHELL_BLOOM_DAMAGE_PADDING = 128.F", self.projection)
        self.assertIn("addTimer(animation.timer)", self.projection)
        self.assertIn("scheduleBloomFrame(monitor)", self.projection)
        self.assertIn("!animation.timer->armed()", self.projection)
        self.assertIn("monitorTransaction()->requestFrame(MONITOR)", self.projection)
        self.assertIn("IT->second.timer->updateTimeout(SHELL_BLOOM_FRAME_INTERVAL)", self.projection)
        self.assertNotIn("damageMonitor(MONITOR)", self.projection)
        scheduler = self.projection[
            self.projection.index("bool CLuminophoreShellProjection::hasAnimatedBloom") :
            self.projection.index("void CLuminophoreShellProjection::discardExpired")
        ]
        self.assertNotIn("effectivePlane == SHELL_PROJECTION_OVERLAY", scheduler)

    def test_shell_plane_does_not_depend_on_client_visibility(self) -> None:
        policy = (ROOT / "src/luminophore/LuminophoreCompositionPolicy.cpp").read_text()
        self.assertNotIn("HAS_VISIBLE_CLIENT", policy)
        self.assertIn("return monitor ? requested : SHELL_PROJECTION_BOTTOM", policy)
        self.assertNotIn("window->m_isFloating ||", policy)
        self.assertIn("SHELL_PROJECTION_BOTTOM", policy)


if __name__ == "__main__":
    unittest.main()
