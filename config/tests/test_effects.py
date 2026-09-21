from __future__ import annotations

import unittest
from types import SimpleNamespace

from luminophore_shell.glow import (
    FrameTimingProbe,
    GlowEdgeBudget,
    GlowFrameCadence,
    GlowRenderFrame,
    GpuFrameTimeline,
)

from luminophore_shell.ui.effects import (
    AccentPulse,
    IconEffectSpec,
    LuminophoreGlowContainer,
    animation_tick_required,
    ripple_sample,
)


class LuminophoreEffectTests(unittest.TestCase):
    @staticmethod
    def _glow_tick_state(high_motion: bool = False) -> SimpleNamespace:
        draws: list[bool] = []
        return SimpleNamespace(
            _tick_id=1,
            _last_frame_seconds=0.0,
            _frame_cadence=GlowFrameCadence(),
            _gpu_timeline=GpuFrameTimeline(),
            _frame_timing_probe=FrameTimingProbe(),
            _accent=SimpleNamespace(active=high_motion),
            get_mapped=lambda: True,
            _needs_tick=lambda: True,
            _transition_pending=lambda: False,
            queue_draw=lambda: draws.append(True),
            draws=draws,
        )

    def test_gsk_keeps_144hz_ambient_stride_and_high_motion_draws_parent(self) -> None:
        clock = SimpleNamespace(
            frame_time=1_000_000,
            get_frame_time=lambda: clock.frame_time,
            get_refresh_info=lambda _time: (6944, 0),
        )
        gsk_state = self._glow_tick_state()
        for _ in range(4):
            LuminophoreGlowContainer._on_glow_frame(gsk_state, None, clock)
            clock.frame_time += 6944
        self.assertEqual(len(gsk_state.draws), 2)

        high_motion_state = self._glow_tick_state(high_motion=True)
        LuminophoreGlowContainer._on_glow_frame(high_motion_state, None, clock)
        self.assertEqual(len(high_motion_state.draws), 1)

    def test_completed_presentation_is_added_to_shared_probe(self) -> None:
        probe = FrameTimingProbe(enabled=True)
        state = SimpleNamespace(
            _frame_timing_probe=probe,
            _last_presentation_counter=-1,
        )
        timing = SimpleNamespace(
            get_complete=lambda: True,
            get_presentation_time=lambda: 1_000_000,
        )
        clock = SimpleNamespace(
            get_frame_counter=lambda: 12,
            get_timings=lambda _counter: timing,
        )
        LuminophoreGlowContainer._record_completed_presentation(state, clock)
        timing.get_presentation_time = lambda: 1_006_944
        clock.get_frame_counter = lambda: 13
        LuminophoreGlowContainer._record_completed_presentation(state, clock)
        sample = probe.snapshot()
        self.assertEqual(sample.presentation_sample_count, 1)
        self.assertEqual(sample.presentation_max_us, 6944)

    def test_render_frame_is_an_immutable_renderer_contract(self) -> None:
        frame = GlowRenderFrame(
            100, 40, 12, 5, 64, 0.16, 0.18, 0.035, 1.5, 0.25,
            (0.1, 0.2, 0.3), (0.8, 0.9, 1.0),
        )
        self.assertEqual(frame.base_color, (0.1, 0.2, 0.3))
        self.assertEqual(frame.edge_budget, (float("inf"),) * 4)
        with self.assertRaisesRegex(Exception, "cannot assign"):
            frame.near_energy = 1.0  # type: ignore[misc]

    def test_edge_mask_geometry_fades_only_inside_the_available_budget(self) -> None:
        bounds, start, end = LuminophoreGlowContainer._edge_mask_geometry("left", 24, 360, 540, 64)
        self.assertEqual((bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()), (-64.0, -64.0, 488.0, 668.0))
        self.assertEqual((start.x, end.x), (-24.0, 0.0))
        self.assertEqual(GlowEdgeBudget(24, 24, 999, 999).constrained(64), ("left", "top"))

    def test_icon_passes_run_far_to_near_to_face(self) -> None:
        spec = IconEffectSpec()

        self.assertEqual(spec.passes, ((4.5, 0.22), (1.75, 0.62), (0.0, 1.0)))
        self.assertGreater(spec.passes[0][0], spec.passes[1][0])
        self.assertGreater(spec.passes[1][0], spec.passes[2][0])

    def test_ripple_radius_grows_and_alpha_fades_to_zero(self) -> None:
        start = ripple_sample(0.0, 100, 40, 20, 10)
        middle = ripple_sample(0.5, 100, 40, 20, 10)
        end = ripple_sample(1.0, 100, 40, 20, 10)

        self.assertLess(start.radius, middle.radius)
        self.assertLess(middle.radius, end.radius)
        self.assertGreater(start.alpha, middle.alpha)
        self.assertEqual(end.alpha, 0)
        self.assertFalse(end.visible)

    def test_unmapped_or_completed_effect_needs_no_tick(self) -> None:
        self.assertFalse(animation_tick_required(False, True, True))
        self.assertFalse(animation_tick_required(True, False, False))
        self.assertTrue(animation_tick_required(True, True, False))
        self.assertTrue(animation_tick_required(True, False, True))

    def test_static_glow_needs_no_tick_and_uses_target_colors(self) -> None:
        state = SimpleNamespace(
            animate_glow=False,
            intensity=2.3,
            _transition_pending=lambda: True,
            _accent=SimpleNamespace(active=True),
            _color=(0.0, 0.0, 0.0),
            _color_target=(0.2, 0.4, 0.6),
            _core_color=(0.0, 0.0, 0.0),
            _core_color_target=(0.8, 0.9, 1.0),
        )
        self.assertFalse(LuminophoreGlowContainer._needs_tick(state))
        colors = LuminophoreGlowContainer._sample_colors(state, 123.0)
        self.assertEqual(colors, (state._color_target, state._core_color_target))

    def test_accent_pulse_uses_shared_320ms_token_and_retargets_without_jump(self) -> None:
        pulse = AccentPulse()
        pulse.trigger(0)
        self.assertAlmostEqual(pulse.sample(80), 0.5)
        self.assertEqual(pulse.sample(160), 1.0)
        before = pulse.sample(220)
        pulse.trigger(220)
        self.assertAlmostEqual(pulse.sample(220), before)
        self.assertGreater(pulse.sample(260), before)

    def test_glow_container_is_owned_by_shared_effect_module(self) -> None:
        self.assertEqual(LuminophoreGlowContainer.__module__, "luminophore_shell.ui.effects")

    def test_glow_geometry_is_reused_until_geometry_inputs_change(self) -> None:
        state = SimpleNamespace(
            corner_radius=12,
            outline_width=5,
            _geometry_key=None,
            _outline=None,
        )

        first = LuminophoreGlowContainer._ensure_geometry(state, 100, 40)
        second = LuminophoreGlowContainer._ensure_geometry(state, 100, 40)
        self.assertIs(first, second)

        state.corner_radius = 16
        third = LuminophoreGlowContainer._ensure_geometry(state, 100, 40)
        self.assertIsNot(first, third)
        self.assertEqual(state._geometry_key, (100, 40, 16, 5))


if __name__ == "__main__":
    unittest.main()
