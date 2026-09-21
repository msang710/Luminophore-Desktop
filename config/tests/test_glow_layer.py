from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
import time
import unittest

from luminophore_shell.glow_layer import GlowLayerController, SpectrumOutput


class FakeMonitor:
    def get_connector(self) -> str:
        return "DP-2"

    def get_geometry(self):
        return SimpleNamespace(x=1920, y=0)


class FakeGlow:
    def external_glow_frame(self, width: int, height: int):
        return SimpleNamespace(
            corner_radius=15.0,
            outline_width=3.0,
            visible_extent=64.0,
            core_energy=0.368,
            phase=0.25,
            base_color=(0.1, 0.2, 0.3),
            core_color=(0.8, 0.9, 1.0),
            edge_budget=(24.0, float("inf"), 41.0, 31.0),
        )


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.pid = 42

    def poll(self):
        return None


class GlowLayerTests(unittest.TestCase):
    def test_widget_bloom_has_dedicated_unblurred_surface_mode(self) -> None:
        root = Path(__file__).parents[1]
        source = (root / "native" / "luminophore_glow_layer.c").read_text()
        rules = (root / "windowrules.lua").read_text()

        self.assertIn("--widget-bloom", source)
        self.assertIn('app->widget_bloom ? "luminophore-native-bloom"', source)
        self.assertIn("draw_bloom_rect(output, &app->rects[i])", source)
        self.assertIn('namespace = "^luminophore-native-bloom(-top)?$"', rules)
        no_blur = rules.split('name = "luminophore-native-bloom-no-blur"', 1)[1]
        self.assertIn("blur = false", no_blur)

    def test_window_demo_uses_an_isolated_layer_namespace(self) -> None:
        root = Path(__file__).parents[1]
        source = (root / "native" / "luminophore_glow_layer.c").read_text()
        rules = (root / "windowrules.lua").read_text()

        self.assertIn('"luminophore-window-glow-demo-top"', source)
        self.assertIn('"luminophore-window-glow-demo"', source)
        self.assertIn('namespace = "^luminophore-window-glow-demo(-top)?$"', rules)

    def test_all_corner_surfaces_use_external_bloom_ownership(self) -> None:
        root = Path(__file__).parents[1]
        app = (root / "luminophore_shell" / "app.py").read_text()
        surface = (root / "luminophore_shell" / "ui" / "surface.py").read_text()

        self.assertEqual(app.count("external_layer_glow=True"), 5)
        self.assertEqual(app.count("external_glow_changed=self._sync_glow_layer"), 5)
        self.assertIn('if getattr(surface, "external_layer_glow", False)', app)
        self.assertNotIn("SubsurfaceGlowRenderer", surface)
        self.assertNotIn("subsurface_glow", surface)
        self.assertNotIn("LUMINOPHORE_GLOW_SUBSURFACE_CANARY", surface)
        self.assertIn("compositor layer order owns occlusion", surface)

    def test_layer_transition_reapplies_input_region_and_routes_glow_from_intent(self) -> None:
        surface = (Path(__file__).parents[1] / "luminophore_shell" / "ui" / "surface.py").read_text()

        self.assertIn('return "top" if self._external_glow_top else "bottom"', surface)
        self.assertIn("def _set_surface_layer(self, overlay: bool, *, advance_revision: bool = True)", surface)
        self.assertIn("self._set_surface_layer(True)", surface)
        self.assertIn("not self._compositor_projected", surface)
        self.assertIn("self.viewport.refresh_input_region()", surface)
        self.assertIn("GLib.idle_add(self._refresh_input_region)", surface)
        self.assertIn("self.window.add_tick_callback(self._refresh_input_region_on_frame)", surface)

    def test_projected_corner_geometry_comes_from_the_live_input_region(self) -> None:
        surface = (Path(__file__).parents[1] / "luminophore_shell" / "ui" / "surface.py").read_text()

        self.assertIn('"panel_x": -1.0', surface)
        self.assertIn('"panel_width": 0.0', surface)
        self.assertNotIn("_projection_geometry_idle", surface)
        self.assertNotIn("_resubmit_projection_geometry", surface)
        geometry_changed = surface[
            surface.index("def _glow_geometry_changed") :
            surface.index("def _on_window_mapped")
        ]
        self.assertIn("self._projection_handshake.start()", geometry_changed)

    def test_geometry_revision_replaces_the_projected_snapshot(self) -> None:
        surface = (Path(__file__).parents[1] / "luminophore_shell" / "ui" / "surface.py").read_text()

        self.assertIn("self._projection_geometry_revision", surface)
        self.assertIn("self._projection_pending_geometry_revision", surface)
        self.assertIn("advance_revision=False", surface)
        self.assertIn("self._projection_geometry_revision = geometry_revision", surface)

    def test_native_widget_bloom_is_disabled_for_the_luminophore_compositor(self) -> None:
        app = (Path(__file__).parents[1] / "luminophore_shell" / "app.py").read_text()

        self.assertIn('widget_bloom=os.environ.get("LUMINOPHORE_COMPOSITOR") != "1"', app)

    def test_projection_success_clears_the_native_widget_bloom_owner(self) -> None:
        surface = (Path(__file__).parents[1] / "luminophore_shell" / "ui" / "surface.py").read_text()
        transition = surface[
            surface.index("self._compositor_projected = projected") :
            surface.index("return projected", surface.index("self._compositor_projected = projected"))
        ]

        self.assertIn("if self.external_layer_glow and self.external_glow_changed is not None", transition)
        self.assertNotIn("and not self._compositor_projected", transition)

    def test_projection_registration_retries_on_frames_and_stops_on_destroy(self) -> None:
        root = Path(__file__).parents[1]
        surface = (root / "luminophore_shell" / "ui" / "surface.py").read_text()
        osd = (root / "luminophore_shell" / "ui" / "osd.py").read_text()
        handshake = (root / "luminophore_shell" / "ui" / "projection.py").read_text()

        self.assertIn("class FrameProjectionHandshake", handshake)
        self.assertIn("add_tick_callback", handshake)
        self.assertIn("if not self.submit()", handshake)
        self.assertIn("remove_tick_callback", handshake)
        self.assertIn("self._projection_handshake.start()", surface)
        self.assertIn("self._projection_handshake.cancel()", surface)
        self.assertIn("self._projection_handshake.start()", osd)
        self.assertIn("self._projection_handshake.cancel()", osd)
        self.assertIn('os.getenv("LUMINOPHORE_COMPOSITOR") != "1"', surface)
        self.assertIn('os.getenv("LUMINOPHORE_COMPOSITOR") != "1"', osd)

    def test_native_renderer_drains_latest_geometry_before_each_frame(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "luminophore_glow_layer.c").read_text()

        self.assertIn("flags | O_NONBLOCK", source)
        render = source.split("static void render_output", 2)[2].split("static void layer_configure", 1)[0]
        self.assertIn("if (app->accept_stdin) read_commands(app);", render)

    def test_native_renderer_uses_demand_driven_split_frame_rates(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "luminophore_glow_layer.c").read_text()

        self.assertIn("#define WIDGET_FRAME_INTERVAL (1.0 / 72.0)", source)
        self.assertIn("#define SPECTRUM_FRAME_INTERVAL (1.0 / 90.0)", source)
        self.assertIn("timerfd_create(CLOCK_MONOTONIC", source)
        self.assertIn("output_frame_interval(output, now)", source)
        self.assertIn("rect_is_visible_or_transitioning", source)
        self.assertIn("spectrum_has_visible_energy", source)
        self.assertIn("output->force_pending = true", source)
        self.assertNotIn("if (output->app->running && !output->closed) render_output(output);", source)

    def test_native_renderer_requires_reveal_protocol_version(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "luminophore_glow_layer.c").read_text()

        self.assertIn('atoi(line + 2) != 5', source)
        self.assertIn("count == 29", source)

    def test_each_widget_reuses_its_own_bloom_pyramid(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "luminophore_glow_layer.c").read_text()

        self.assertIn("struct widget_bloom_slot", source)
        self.assertIn("widget_bloom_for(app, rect)", source)
        self.assertIn("slot->initialized", source)

    def test_spectrum_layer_stays_below_normal_windows(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "luminophore_glow_layer.c").read_text()

        self.assertIn("output->top ? ZWLR_LAYER_SHELL_V1_LAYER_TOP", source)
        self.assertIn("ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM,", source)
        self.assertIn('output->top ? "luminophore-native-bloom-top"', source)
        self.assertIn("top->top = true", source)

    def test_spectrum_peak_reaches_one_third_of_each_output(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "luminophore_glow_layer.c").read_text()

        self.assertIn("output->height / 3.0f", source)
        self.assertIn("height = 2.0f + level * (max_height - 2.0f)", source)
        self.assertNotIn("level * 70.0f", source)

    def test_stale_audio_releases_smoothly_instead_of_replaying_ring_tail(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "luminophore_glow_layer.c").read_text()

        self.assertIn("sequence != app->last_spectrum_sequence", source)
        self.assertIn("now - app->last_spectrum_audio_at < 0.15", source)
        self.assertIn("float tau = target > app->spectrum_bands[band] ? 0.090f : 0.280f", source)
        self.assertIn("target == 0.0f && app->spectrum_bands[band] < 0.0005f", source)
        self.assertNotIn("app->spectrum_silent ? 0.0f", source)
        self.assertIn("2.0f + level * (max_height - 2.0f)", source)

    def test_payload_uses_monitor_local_geometry_and_bounded_edge_budget(self) -> None:
        surface = SimpleNamespace(
            monitor=FakeMonitor(),
            glow=FakeGlow(),
            global_bounds=lambda: (1944, 24, 560, 124),
        )
        payload, count = GlowLayerController.payload({"weather": surface})

        self.assertEqual(count, 1)
        self.assertTrue(payload.startswith("V 5\nB\nR DP-2 weather bottom 0 24 24 560 124 "))
        self.assertIn("24.000000 100000.000000 41.000000 31.000000", payload)
        self.assertTrue(payload.endswith("C\n"))

    def test_payload_omits_inactive_surface(self) -> None:
        surface = SimpleNamespace(
            monitor=FakeMonitor(),
            glow=FakeGlow(),
            global_bounds=lambda: (1944, 24, 560, 124),
            external_glow_active=lambda: False,
        )

        payload, count = GlowLayerController.payload({"weather": surface})

        self.assertEqual(count, 0)
        self.assertEqual(payload, "V 5\nB\nC\n")

    def test_payload_routes_active_surface_to_top_layer(self) -> None:
        surface = SimpleNamespace(
            monitor=FakeMonitor(),
            glow=FakeGlow(),
            global_bounds=lambda: (1944, 24, 560, 124),
            external_glow_layer=lambda: "top",
        )

        payload, count = GlowLayerController.payload({"weather": surface})

        self.assertEqual(count, 1)
        self.assertIn("R DP-2 weather top 0 24 24 560 124", payload)

    def test_payload_carries_surface_geometry_revision(self) -> None:
        surface = SimpleNamespace(
            monitor=FakeMonitor(),
            glow=FakeGlow(),
            global_bounds=lambda: (1944, 24, 560, 124),
            external_glow_revision=lambda: 37,
        )

        payload, count = GlowLayerController.payload({"weather": surface})

        self.assertEqual(count, 1)
        self.assertIn("R DP-2 weather bottom 37 24 24 560 124", payload)

    def test_payload_carries_reversible_overview_reveal_command(self) -> None:
        surface = SimpleNamespace(
            monitor=FakeMonitor(),
            glow=FakeGlow(),
            global_bounds=lambda: (1944, 24, 560, 124),
            external_glow_reveal=lambda: (0.25, 1.0, 12.5, 0.16, -10.0, 0.0),
        )

        payload, count = GlowLayerController.payload({"weather": surface})

        self.assertEqual(count, 1)
        self.assertIn("0.250000 1.000000 12.500000 0.160000 -10.000000 0.000000", payload)

    def test_status_reader_tracks_accepted_and_rendered_revision_per_surface(self) -> None:
        controller = GlowLayerController(Path("missing"), lambda _reason: None)
        controller.process = SimpleNamespace(
            stdout=io.StringIO("G launcher 12 0\nG launcher 12 12\n"),
        )

        controller._read_status()

        self.assertEqual(
            controller.geometry_revisions["launcher"],
            {"accepted": 12, "rendered": 12},
        )

    def test_payload_carries_continuous_desktop_axis_and_color_stops(self) -> None:
        outputs = (
            SpectrumOutput("DP-1", 0, 1920, 0, 3840, (0.1, 0.2, 0.3), (0.8, 0.4, 0.2)),
            SpectrumOutput("DP-2", 1920, 1920, 0, 3840, (0.8, 0.4, 0.2), (0.2, 0.5, 0.9)),
        )

        payload, count = GlowLayerController.payload({}, outputs)

        self.assertEqual(count, 0)
        self.assertIn("M DP-1 0 1920 0 3840 0.100000 0.200000 0.300000 0.800000 0.400000 0.200000", payload)
        self.assertIn("M DP-2 1920 1920 0 3840 0.800000 0.400000 0.200000 0.200000 0.500000 0.900000", payload)

    def test_sync_is_change_bounded_and_reports_process_exit_once(self) -> None:
        failures: list[str] = []
        controller = GlowLayerController(Path("missing"), failures.append)
        process = FakeProcess()
        controller.process = process  # type: ignore[assignment]
        surface = SimpleNamespace(
            monitor=FakeMonitor(), glow=FakeGlow(), global_bounds=lambda: (1944, 24, 560, 124),
        )

        self.assertTrue(controller.sync({"weather": surface}))
        for _ in range(100):
            first = process.stdin.getvalue()
            if first:
                break
            time.sleep(0.001)
        self.assertTrue(controller.sync({"weather": surface}))
        self.assertEqual(process.stdin.getvalue(), first)

        process.poll = lambda: 1  # type: ignore[method-assign]
        self.assertFalse(controller.sync({"weather": surface}))
        self.assertFalse(controller.sync({"weather": surface}))
        self.assertEqual(failures, ["renderer process exited"])


if __name__ == "__main__":
    unittest.main()
