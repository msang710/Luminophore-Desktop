from __future__ import annotations

from pathlib import Path
import unittest


class WindowGlowPluginTests(unittest.TestCase):
    def test_native_luminophore_compositor_does_not_load_legacy_glow_plugin(self) -> None:
        autostart = Path("autostart.lua").read_text(encoding="utf-8")
        guard = 'if os.getenv("LUMINOPHORE_COMPOSITOR") ~= "1" then'
        loader = "scripts/load-window-glow-plugin"
        self.assertIn(guard, autostart)
        self.assertIn(loader, autostart)
        self.assertLess(autostart.index(guard), autostart.index(loader))

    def test_plugin_attaches_to_existing_and_new_normal_windows(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("for (const auto& window : Desktop::windowState()->windows())", source)
        self.assertIn("m_events.window.openLate.listen", source)
        self.assertIn("m_events.window.close.listen", source)
        self.assertIn("if (!window || !window->m_isMapped || isAttached(window))", source)
        self.assertNotIn("isDemoWindow", source)
        self.assertIn("DECORATION_LAYER_OVER", source)
        self.assertIn("geometricBox(IGeometric::GEOMETRIC_CURRENT)", source)
        self.assertIn("g_pHyprRenderer->damageWindow(window, true)", source)
        self.assertIn("m_renderPass.add(makeUnique<CLuminophoreGlowPassElement>", source)
        self.assertIn('removeAllOfType("CLuminophoreGlowPassElement")', source)

    def test_focus_and_fullscreen_lifecycle_are_explicit(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("m_events.window.active.listen", source)
        self.assertIn("damageAttachedWindows()", source)
        self.assertIn("m_events.window.fullscreen.listen", source)
        self.assertIn("m_events.window.floating.listen", source)
        render_predicate = source.split("static bool shouldRenderGlow", 1)[1].split("static void breathingTick", 1)[0]
        self.assertNotIn("Fullscreen::controller()->isFullscreen", render_predicate)
        self.assertIn("including true fullscreen clients", source)
        self.assertIn("removeWindowDecoration(PHANDLE, attached->decoration)", source)

    def test_true_fullscreen_reinserts_the_inset_pass_after_the_client(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("m_events.render.stage.listen", source)
        self.assertIn("stage == RENDER_POST_WINDOW", source)
        self.assertIn("enqueueFullscreenGlow()", source)
        self.assertIn("renderdata.decorate=false", source)
        self.assertIn("attached->decoration->draw(monitor, 1.0F)", source)

    def test_glow_is_inset_into_the_client_and_reserves_no_outer_space(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn(".desiredExtents = {{0, 0}, {0, 0}}", source)
        self.assertIn("renderLuminophoreInnerGlow(box", source)
        self.assertIn("2.6F * 0.070F * dynamics.intensity", source)
        self.assertIn("2.6F * 0.29F * dynamics.intensity", source)
        self.assertIn("40.0F * dynamics.radius", source)
        self.assertIn("window->rounding()", source)
        self.assertIn("window->roundingPower()", source)
        self.assertIn("distanceFromRoundedEdge = max(0.0, -sdfDistance)", source)
        self.assertIn("fragColor = vec4(0.0, 0.0, 0.0, 1.0)", source)
        self.assertIn("const bool floating = window->m_isFloating", source)
        self.assertIn("if (!floating)", source)
        self.assertIn("renderTiledCornerMask(box, rounding, roundingPower)", source)
        self.assertIn("uniform int discardOpaque;", source)
        self.assertNotIn("uniform int renderMask;", source)
        self.assertIn("if (discardOpaque == 0)", source)
        self.assertIn("if (discardOpaque == 1)", source)
        self.assertIn("renderMask ? 1 : 0", source)
        self.assertIn("CHyprColor(0.0F, 0.0F, 0.0F, 1.0F)", source)
        self.assertIn("useShader(previousShader)", source)
        self.assertIn("using Hyprland fallback", source)
        self.assertNotIn("box.copy().expand(spread)", source)
        self.assertNotIn("renderBorder(", source)

    def test_tiled_mask_and_glow_are_separate_render_operations(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        draw_pass = source.split("void drawPass(PHLMONITOR monitor, float const& alpha)", 1)[1]
        draw_pass = draw_pass.split("eDecorationType getDecorationType()", 1)[0]
        self.assertEqual(draw_pass.count("renderTiledCornerMask("), 1)
        self.assertEqual(draw_pass.count("renderLuminophoreInnerGlow("), 2)
        self.assertLess(draw_pass.index("renderTiledCornerMask("), draw_pass.index("renderLuminophoreInnerGlow("))
        self.assertIn("if (renderMask)\n            return;", source)

    def test_active_state_selects_semantic_palette_and_breathing(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("focusState()->isWindowActive(window)", source)
        self.assertIn("active ? luminophoreGlowDynamics()", source)
        self.assertIn(".intensity = 0.82F, .radius = 1.0F", source)
        self.assertIn("DP-2 raw_primary #9CCBFB", source)
        self.assertIn("DP-1 raw_primary #FFB77D", source)

    def test_only_active_window_is_damaged_by_72hz_breathing_timer(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("std::chrono::milliseconds(14)", source)
        self.assertIn("const auto window = Desktop::focusState()->window()", source)
        self.assertIn("shouldRenderGlow(window) && isAttached(window)", source)
        self.assertIn("g_pHyprRenderer->damageWindow(window, true)", source)
        self.assertIn("g_pEventLoopManager->addTimer(PBREATHTIMER)", source)
        self.assertIn("g_pEventLoopManager->removeTimer(PBREATHTIMER)", source)
        self.assertIn("PBREATHTIMER->cancel()", source)

    def test_active_breathing_matches_current_widget_bloom_timing(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("const float period = 4.9F + (6.4F - 4.9F)", source)
        self.assertIn("const float rise = 0.32F + (0.44F - 0.32F)", source)
        self.assertIn(".intensity = 0.82F + (1.54F - 0.82F) * breath", source)

    def test_plugin_does_not_poll_hyprctl_or_create_a_layer_surface(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertNotIn("hyprctl", source)
        self.assertNotIn("zwlr_layer_shell", source)

    def test_plugin_rejects_mismatched_hyprland_headers(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("__hyprland_api_get_hash()", source)
        self.assertIn("__hyprland_api_get_client_hash()", source)
        self.assertIn("if (compositorHash != clientHash)", source)
        self.assertIn("Hyprland header version mismatch", source)

    def test_plugin_is_installed_and_loaded_from_stable_user_data_path(self) -> None:
        root = Path(__file__).parents[1]
        installer = (root / "scripts" / "install-window-glow-plugin").read_text()
        loader = (root / "scripts" / "load-window-glow-plugin").read_text()
        autostart = (root / "autostart.lua").read_text()

        self.assertIn('luminophore-shell/plugins', installer)
        self.assertIn('install -m 755', installer)
        self.assertIn('sha256sum', installer)
        self.assertIn('luminophore-window-glow-plugin-$plugin_hash.so', installer)
        self.assertIn('readlink -f', loader)
        self.assertIn('luminophore-window-glow-plugin.loaded', loader)
        self.assertIn('Plugin LUMINOPHORE Window Glow by louise:', loader)
        self.assertIn('hyprctl plugin load "$plugin"', loader)
        self.assertIn('scripts/load-window-glow-plugin', autostart)

    def test_session_bootstrap_damages_outputs_only_three_times(self) -> None:
        source = (Path(__file__).parents[1] / "native" / "window_glow_plugin.cpp").read_text()

        self.assertIn("std::chrono::milliseconds(100)", source)
        self.assertIn("std::chrono::milliseconds(200)", source)
        self.assertIn("std::chrono::milliseconds(500)", source)
        self.assertIn("State::monitorState()->monitors()", source)
        self.assertIn("g_pHyprRenderer->damageMonitor(monitor)", source)
        self.assertIn("PBOOTSTRAPSTEP >= BOOTSTRAP_DAMAGE_DELAYS.size()", source)
        self.assertIn("self->cancel()", source)


if __name__ == "__main__":
    unittest.main()
