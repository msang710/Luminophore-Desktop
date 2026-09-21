from __future__ import annotations

import unittest

from luminophore_shell.glow import (
    FrameTimingProbe,
    GlowEdgeBudget,
    GlowFrameCadence,
    GLOW_TIME_WRAP_SECONDS,
    GPU_TIMELINE_MAX_DELTA_SECONDS,
    GpuFrameTimeline,
    frame_profile,
    glow_dynamics,
    glow_falloff,
    glow_frame_stride,
    parse_hex_rgb,
    surface_glow_phase,
)


class GlowRendererTests(unittest.TestCase):
    def test_edge_budget_uses_actual_panel_distance_to_each_viewport_edge(self) -> None:
        budget = GlowEdgeBudget.from_bounds(1920, 1080, 24, 24, 360, 540)
        self.assertEqual(budget.as_tuple(), (24.0, 24.0, 1536.0, 516.0))
        self.assertEqual(budget.constrained(64), ("left", "top"))
        self.assertEqual(GlowEdgeBudget().constrained(64), ())

    def test_hex_colors_are_parsed_as_normalized_rgb(self) -> None:
        self.assertEqual(parse_hex_rgb("#FF8000"), (1.0, 128 / 255, 0.0))
        with self.assertRaises(ValueError):
            parse_hex_rgb("#1234")
        with self.assertRaises(ValueError):
            parse_hex_rgb("#GG0000")

    def test_glow_uses_wide_and_near_continuous_falloff_passes(self) -> None:
        passes = glow_falloff(64, 2.3, elapsed_seconds=10, phase=0.2)

        self.assertEqual(len(passes), 2)
        self.assertGreater(passes[0].blur_radius, passes[1].blur_radius)
        self.assertLess(passes[0].alpha, passes[1].alpha)
        self.assertTrue(all(0 < glow_pass.alpha <= 0.52 for glow_pass in passes))

    def test_glow_animation_changes_slowly_without_turning_off(self) -> None:
        start = glow_falloff(40, 1.0, elapsed_seconds=10.0, phase=0.3)
        next_frame = glow_falloff(40, 1.0, elapsed_seconds=10.033, phase=0.3)
        later = glow_falloff(40, 1.0, elapsed_seconds=18.0, phase=0.3)

        self.assertLess(abs(start[-1].alpha - next_frame[-1].alpha), 0.01)
        self.assertNotEqual(start[-1].alpha, later[-1].alpha)
        self.assertGreater(min(glow_pass.alpha for glow_pass in start), 0)

    def test_zero_intensity_has_no_render_layers(self) -> None:
        self.assertEqual(glow_falloff(40, 0, elapsed_seconds=0), ())

    def test_surface_phases_are_stable_and_separated(self) -> None:
        phases = {surface_glow_phase(name) for name in ("launcher", "notifications", "weather", "system")}
        self.assertEqual(len(phases), 4)
        self.assertEqual(surface_glow_phase("custom"), surface_glow_phase("custom"))

    def test_frame_profile_keeps_falloff_on_one_geometry_contract(self) -> None:
        profile = frame_profile(64, 2.3, 5, 12, 10, 0.2)

        self.assertEqual(profile.outline_width, 5)
        self.assertEqual(profile.corner_radius, 12)
        self.assertEqual(len(profile.bloom_passes), 2)

    def test_frame_stride_uses_native_divisors_nearest_ambient_target(self) -> None:
        cases = {
            60: 1,
            90: 1,
            120: 2,
            144: 2,
            165: 2,
        }
        for refresh_hz, expected_stride in cases.items():
            with self.subTest(refresh_hz=refresh_hz):
                interval_us = round(1_000_000 / refresh_hz)
                self.assertEqual(glow_frame_stride(interval_us, False), expected_stride)
                self.assertEqual(glow_frame_stride(interval_us, True), 1)

    def test_frame_stride_fails_safe_for_invalid_inputs(self) -> None:
        self.assertEqual(glow_frame_stride(0, False), 1)
        self.assertEqual(glow_frame_stride(-1, False), 1)
        self.assertEqual(glow_frame_stride(6944, False, float("nan")), 1)
        self.assertEqual(glow_frame_stride(6944, False, float("inf")), 1)
        self.assertEqual(glow_frame_stride(6944, False, 0), 1)

    def test_frame_cadence_switches_between_144hz_ambient_and_high_motion(self) -> None:
        cadence = GlowFrameCadence()

        self.assertEqual([cadence.should_draw(6944, False) for _ in range(6)], [True, False, True, False, True, False])
        self.assertEqual([cadence.should_draw(6944, True) for _ in range(3)], [True, True, True])
        self.assertEqual([cadence.should_draw(6944, False) for _ in range(4)], [True, False, True, False])
        cadence.reset()
        self.assertTrue(cadence.should_draw(6944, False))

    def test_gpu_timeline_starts_at_zero_clamps_stalls_and_resets(self) -> None:
        timeline = GpuFrameTimeline()
        self.assertEqual(timeline.advance(1_000_000), 0.0)
        self.assertAlmostEqual(timeline.advance(1_006_944), 0.006944)
        before_stall = timeline.elapsed_seconds
        timeline.advance(1_506_944)
        self.assertEqual(timeline.last_delta_seconds, GPU_TIMELINE_MAX_DELTA_SECONDS)
        self.assertAlmostEqual(
            timeline.elapsed_seconds,
            before_stall + GPU_TIMELINE_MAX_DELTA_SECONDS,
        )
        self.assertEqual(timeline.advance(1_400_000), timeline.elapsed_seconds)
        self.assertEqual(timeline.last_delta_seconds, 0.0)
        timeline.advance(1_513_888)
        self.assertAlmostEqual(timeline.last_delta_seconds, 0.006944)
        timeline.reset()
        self.assertEqual(timeline.advance(9_000_000), 0.0)

    def test_gpu_timeline_wrap_preserves_all_breathing_phases(self) -> None:
        before = glow_dynamics(0.0, 0.29)
        after = glow_dynamics(GLOW_TIME_WRAP_SECONDS, 0.29)
        self.assertAlmostEqual(before.intensity_factor, after.intensity_factor, places=12)
        self.assertAlmostEqual(before.radius_factor, after.radius_factor, places=12)

    def test_frame_timing_probe_is_opt_in_bounded_and_reports_misses(self) -> None:
        disabled = FrameTimingProbe()
        disabled.record(1_000_000, 6944)
        self.assertEqual(disabled.snapshot().sample_count, 0)

        probe = FrameTimingProbe(enabled=True, capacity=4)
        timestamp = 1_000_000
        probe.record(timestamp, 6944)
        for interval in (6900, 7000, 20_000, 6800, 7100):
            timestamp += interval
            probe.record(timestamp, 6944)
        sample = probe.snapshot()
        self.assertEqual(sample.sample_count, 4)
        self.assertEqual(sample.refresh_interval_us, 6944)
        self.assertGreater(sample.p99_us, sample.p50_us)
        self.assertEqual(sample.missed_frame_ratio, 0.25)

    def test_frame_timing_probe_correlates_events_with_rare_severe_stalls(self) -> None:
        probe = FrameTimingProbe(enabled=True)
        probe.record(1_000_000, 6944)
        probe.event("content.label_update")
        probe.event("surface.measure", 2_400_000)
        probe.record(1_020_000, 6944)
        sample = probe.snapshot()

        self.assertEqual(sample.max_interval_us, 20_000)
        self.assertEqual(sample.severe_stall_count, 1)
        self.assertEqual(sample.max_consecutive_missed, 1)
        self.assertEqual(sample.event_counts, {
            "content.label_update": 1,
            "surface.measure": 1,
        })
        self.assertEqual(sample.event_duration_us, {"surface.measure": 2400.0})
        self.assertEqual(sample.recent_stalls[0]["severity"], "severe")
        self.assertEqual(
            sample.recent_stalls[0]["events"]["content.label_update"], 1
        )

    def test_frame_timing_probe_reports_completed_presentation_intervals(self) -> None:
        probe = FrameTimingProbe(enabled=True)
        for timestamp in (1_000_000, 1_006_944, 1_020_000):
            probe.record_presentation(timestamp)
        sample = probe.snapshot()
        self.assertEqual(sample.presentation_sample_count, 2)
        self.assertEqual(sample.presentation_max_us, 13_056)
        self.assertGreater(sample.presentation_p95_us, 12_000)

    def test_frame_timing_probe_stop_drops_samples_and_events(self) -> None:
        probe = FrameTimingProbe(enabled=True)
        probe.event("content.label_update")
        probe.record(1_000_000, 6944)
        probe.configure(False)
        probe.event("content.label_update")
        probe.record(1_020_000, 6944)
        sample = probe.snapshot()
        self.assertEqual(sample.sample_count, 0)
        self.assertEqual(sample.event_counts, {})


if __name__ == "__main__":
    unittest.main()
