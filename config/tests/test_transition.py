from __future__ import annotations

import unittest
from types import SimpleNamespace

import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell

from luminophore_shell.transition import (
    MotionSpec,
    MotionTokens,
    OverviewRevealTransition,
    TimedTransition,
    VanishingTransition,
    ease_in_cubic,
    ease_out_cubic,
    smoothstep,
)
from luminophore_shell.glow import FrameTimingProbe
from luminophore_shell.ui.surface import (
    AnimatedSurfaceContent,
    ExpansionCoordinator,
    anchored_panel_bounds,
    expanded_keyboard_mode,
    transition_content_opacities,
)


class TimedTransitionTests(unittest.TestCase):
    def test_surface_transition_records_completed_presentation(self) -> None:
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

        AnimatedSurfaceContent._record_completed_presentation(state, clock)
        timing.get_presentation_time = lambda: 1_006_944
        clock.get_frame_counter = lambda: 13
        AnimatedSurfaceContent._record_completed_presentation(state, clock)

        sample = probe.snapshot()
        self.assertEqual(sample.presentation_sample_count, 1)
        self.assertEqual(sample.presentation_max_us, 6944)

    def test_smoothstep_is_bounded_and_symmetric(self) -> None:
        self.assertEqual(smoothstep(-1), 0)
        self.assertEqual(smoothstep(2), 1)
        self.assertAlmostEqual(smoothstep(0.5), 0.5)
        self.assertAlmostEqual(smoothstep(0.25), 1 - smoothstep(0.75))

    def test_transition_reaches_target(self) -> None:
        transition = TimedTransition(0, 200)
        self.assertTrue(transition.retarget(1, 1000, span=1))
        self.assertEqual(transition.sample(1000).value, 0)
        self.assertAlmostEqual(transition.sample(1100).value, 0.5)
        completed = transition.sample(1200)
        self.assertEqual(completed.value, 1)
        self.assertFalse(completed.active)

    def test_retarget_continues_from_current_value(self) -> None:
        transition = TimedTransition(0, 200)
        transition.retarget(1, 0, span=1)
        before = transition.sample(100).value
        self.assertAlmostEqual(before, 0.5)
        transition.retarget(0, 100, span=1)
        self.assertAlmostEqual(transition.sample(100).value, before)
        self.assertGreater(transition.sample(125).value, 0)
        completed = transition.sample(200)
        self.assertEqual(completed.value, 0)
        self.assertFalse(completed.active)

    def test_remaining_distance_scales_duration(self) -> None:
        transition = TimedTransition(0.75, 200)
        transition.retarget(1, 10, span=1)
        self.assertTrue(transition.sample(59).active)
        self.assertFalse(transition.sample(60).active)
        self.assertEqual(transition.value, 1)

    def test_zero_duration_snaps(self) -> None:
        transition = TimedTransition(0, 0)
        self.assertFalse(transition.retarget(1, 0, span=1))
        self.assertEqual(transition.value, 1)
        self.assertFalse(transition.active)

    def test_surface_motion_distributes_geometry_across_the_transition(self) -> None:
        tokens = MotionTokens.from_config(200, 600)

        self.assertEqual(tokens.expand.duration_ms, 200)
        self.assertEqual(tokens.collapse.duration_ms, 180)
        self.assertEqual(tokens.palette.duration_ms, 600)
        self.assertEqual(tokens.hover.duration_ms, 180)
        self.assertEqual(tokens.ripple.duration_ms, 320)
        self.assertIs(tokens.expand.easing, smoothstep)
        self.assertIs(tokens.collapse.easing, smoothstep)
        expand_samples = [tokens.expand.easing(frame / 12) for frame in range(13)]
        frame_steps = [later - earlier for earlier, later in zip(expand_samples, expand_samples[1:])]
        self.assertLess(max(frame_steps), 0.13)

    def test_new_motion_is_used_for_next_retarget_without_value_jump(self) -> None:
        transition = TimedTransition(0, 200)
        transition.retarget(1, 0, span=1)
        before = transition.sample(80).value
        transition.set_motion(MotionSpec(100, ease_in_cubic))
        transition.retarget(0, 80, span=1)

        self.assertAlmostEqual(transition.sample(80).value, before)
        self.assertFalse(transition.sample(180).active)

    def test_overview_reveal_is_offset_only_while_translucent(self) -> None:
        transition = OverviewRevealTransition(-10, 0)
        transition.snap(False)
        transition.retarget(True, 1000, 1000, 160, ease_out_cubic)

        start = transition.sample(1000)
        middle = transition.sample(1080)
        end = transition.sample(1160)

        self.assertEqual((start.opacity, start.offset_x), (0.0, -10.0))
        self.assertGreater(middle.opacity, 0.5)
        self.assertLess(abs(middle.offset_x), 5.0)
        self.assertEqual((end.opacity, end.offset_x), (1.0, 0.0))

    def test_overview_reveal_reverses_from_current_value(self) -> None:
        transition = OverviewRevealTransition(10, 0)
        transition.snap(False)
        transition.retarget(True, 0, 0, 160, ease_out_cubic)
        before = transition.sample(60).opacity
        transition.retarget(False, 60, 60, 110, ease_in_cubic)

        self.assertAlmostEqual(transition.sample(60).opacity, before)
        self.assertEqual(transition.sample(170).opacity, 0.0)

    def test_vanishing_transition_shares_smooth_collapse_and_backdrop_progress(self) -> None:
        transition = VanishingTransition(200)
        self.assertEqual(transition.duration_ms, 180)
        transition.begin(1000)
        start = transition.sample(1000)
        middle = transition.sample(1090)
        end = transition.sample(1180)
        self.assertEqual(start.scale, 1.0)
        self.assertEqual(middle.opacity, middle.backdrop_alpha)
        self.assertGreater(middle.scale, 0)
        self.assertEqual(end.scale, 0)
        self.assertFalse(end.active)

    def test_expand_and_collapse_use_directional_content_fades(self) -> None:
        _collapsed, expanding = transition_content_opacities(0.3, True, True)
        _collapsed, collapsing_start = transition_content_opacities(0.9, False, True)
        _collapsed, collapsing_late = transition_content_opacities(0.6, False, True)

        self.assertGreater(expanding, 0)
        self.assertGreater(collapsing_start, collapsing_late)
        self.assertEqual(collapsing_late, 0)

    def test_panel_bounds_keep_the_requested_anchor(self) -> None:
        self.assertEqual(anchored_panel_bounds("left", 1000, 220, 40), (0, 0, 220, 40))
        self.assertEqual(anchored_panel_bounds("right", 1000, 220, 40), (780, 0, 220, 40))

    def test_panel_bounds_clamp_to_fixed_viewport(self) -> None:
        self.assertEqual(anchored_panel_bounds("right", 1000, 1200, 40), (0, 0, 1000, 40))

    def test_panel_bounds_keep_visual_margin_inside_full_monitor_viewport(self) -> None:
        self.assertEqual(
            anchored_panel_bounds("left", 1920, 360, 60, edge_inset=24, top_inset=24),
            (24, 24, 360, 60),
        )
        self.assertEqual(
            anchored_panel_bounds("right", 1920, 236, 45, edge_inset=24, top_inset=24),
            (1660, 24, 236, 45),
        )

    def test_settings_keyboard_mode_is_on_demand(self) -> None:
        self.assertEqual(
            expanded_keyboard_mode(False, True),
            Gtk4LayerShell.KeyboardMode.ON_DEMAND,
        )
        self.assertEqual(
            expanded_keyboard_mode(False, False),
            Gtk4LayerShell.KeyboardMode.NONE,
        )
        self.assertEqual(
            expanded_keyboard_mode(True, False),
            Gtk4LayerShell.KeyboardMode.EXCLUSIVE,
        )

    def test_overview_group_can_open_multiple_surfaces_on_one_monitor(self) -> None:
        coordinator = ExpansionCoordinator()
        monitor = object()

        class Surface:
            def __init__(self) -> None:
                self.monitor = monitor
                self.expanded = False

            def set_expanded(self, expanded: bool) -> None:
                if self.expanded == expanded:
                    return
                self.expanded = expanded
                if expanded:
                    coordinator.request_open(self)
                else:
                    coordinator.closed(self)

        first, second, direct = Surface(), Surface(), Surface()
        coordinator.open_group([first, second])
        self.assertTrue(first.expanded)
        self.assertTrue(second.expanded)

        direct.set_expanded(True)
        self.assertFalse(first.expanded)
        self.assertFalse(second.expanded)
        self.assertTrue(direct.expanded)


if __name__ == "__main__":
    unittest.main()
