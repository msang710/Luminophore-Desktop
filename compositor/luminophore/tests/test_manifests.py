import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LUMINOPHORE = ROOT / "luminophore"


class LuminophoreBuildSubstrateTests(unittest.TestCase):
    def test_nested_canary_can_exclude_drm_backend(self) -> None:
        compositor = (ROOT / "src" / "Compositor.cpp").read_text()

        self.assertIn('Env::envEnabled("LUMINOPHORE_NESTED_ONLY")', compositor)
        self.assertIn("if (!headlessOnly && !nestedOnly)", compositor)
        self.assertIn("nestedOnly ? Aquamarine::eBackendRequestMode::AQ_BACKEND_REQUEST_MANDATORY", compositor)

    def test_headless_runner_excludes_drm_and_parent_wayland_backends(self) -> None:
        compositor = (ROOT / "src/Compositor.cpp").read_text()
        backend = compositor[compositor.index("std::vector<Aquamarine::SBackendImplementationOptions>"):compositor.index("m_aqBackend = CBackend::create")]
        self.assertIn('Env::envEnabled("HYPRLAND_HEADLESS_ONLY")', backend)
        self.assertIn("if (!headlessOnly && !nestedOnly)", backend)
        self.assertIn("if (!headlessOnly) {", backend)
        self.assertLess(backend.index("if (!headlessOnly) {"), backend.index("AQ_BACKEND_WAYLAND"))
        self.assertIn("AQ_BACKEND_HEADLESS", backend)

    def test_upstream_lock_matches_repository_and_version(self) -> None:
        lock = json.loads((LUMINOPHORE / "upstream.lock").read_text())
        self.assertEqual(lock["schema"], "luminophore-upstream-lock/v1")
        self.assertEqual(lock["version"], (ROOT / "VERSION").read_text().strip())
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", lock["commit"], "HEAD"],
            cwd=ROOT,
            check=True,
        )

    def test_source_policy_never_activates_live_session(self) -> None:
        policy = json.loads((LUMINOPHORE / "build-manifest.json").read_text())
        self.assertFalse(policy["live_install"])
        self.assertFalse(policy["default_session_mutation"])

    def test_patch_manifest_is_bound_to_lock(self) -> None:
        lock = json.loads((LUMINOPHORE / "upstream.lock").read_text())
        patches = json.loads((LUMINOPHORE / "patch-manifest.json").read_text())
        self.assertEqual(patches["upstream_commit"], lock["commit"])
        self.assertEqual(patches["series"][0]["feature_id"], "NC-PR1")

    def test_canary_wrapper_requires_colocated_artifact(self) -> None:
        wrapper = (LUMINOPHORE / "scripts" / "luminophore-hyprland-canary").read_text()
        self.assertIn('session="$script_dir/luminophore-compositor-session"', wrapper)
        self.assertIn("incomplete LUMINOPHORE canary artifact", wrapper)
        self.assertIn("export LUMINOPHORE_COMPOSITOR=1", wrapper)
        self.assertIn("exec \"$session\" \"$@\"", wrapper)

    def test_runtime_builder_owns_all_session_executables(self) -> None:
        builder = (LUMINOPHORE / "scripts" / "build-canary").read_text()
        self.assertIn("--target Hyprland hyprctl start-hyprland", builder)
        self.assertIn('install -m 755 "$hyprctl_binary"', builder)
        self.assertIn('install -m 755 "$watchdog_binary"', builder)
        self.assertIn('schema:"luminophore-runtime-artifact/v1"', builder)
        self.assertIn("runtime_dependencies", builder)
        self.assertIn("source_state_sha256", builder)
        self.assertIn("runtime_files", builder)
        self.assertIn('install -m 755 "$source_root/luminophore/scripts/verify-spatial-transition"', builder)
        self.assertIn('"verify-spatial-transition":{sha256:$verifier}', builder)
        self.assertIn('install -m 755 "$source_root/luminophore/scripts/run-spatial-canary-suite"', builder)
        self.assertIn('"run-spatial-canary-suite":{sha256:$suite}', builder)
        self.assertIn('spatial_contract:"luminophore-spatial-evidence/v2"', builder)
        self.assertIn("audit_read_only:true", builder)
        self.assertIn("exercise_opt_in:true", builder)

    def test_runtime_session_fails_closed_and_uses_colocated_processes(self) -> None:
        launcher = (LUMINOPHORE / "scripts" / "luminophore-compositor-session").read_text()
        self.assertIn('digest mismatch for $name', launcher)
        self.assertIn('export LUMINOPHORE_COMPOSITOR_ROOT="$generation_root"', launcher)
        self.assertIn('export LUMINOPHORE_SHELL_BLOOM="$luminophore_effects"', launcher)
        self.assertIn('exec "$bin_dir/start-hyprland" --path "$bin_dir/Hyprland"', launcher)
        self.assertIn('LUMINOPHORE_CONFIG_PATH', launcher)
        self.assertIn('-- --config "$config_path"', launcher)
        self.assertNotIn("/usr/bin/start-hyprland", launcher)
        self.assertIn(".runtime_files | keys[]", launcher)

    def test_spatial_transition_verifier_requires_committed_geometry(self) -> None:
        verifier = (LUMINOPHORE / "scripts" / "verify-spatial-transition").read_text()
        self.assertIn(".committed == true", verifier)
        self.assertIn("$root.committedRevision.model == $root.revision", verifier)
        self.assertIn("$root.committedRevision.topology == $root.topologyRevision", verifier)
        self.assertIn("union_box([.fragments[].box]) == $window.box", verifier)
        self.assertIn("any(.fragments[]; .output == $window.primaryOutput)", verifier)
        self.assertIn("contains(.box; $fragment.box)", verifier)
        self.assertIn("LUMINOPHORE_EXPECT_OUTPUTS", verifier)
        self.assertIn("LUMINOPHORE_EXPECT_VIEW", verifier)

    def test_package_template_preserves_external_compatibility_dependencies(self) -> None:
        package = (LUMINOPHORE / "packaging" / "arch" / "PKGBUILD.in").read_text()
        notices = (LUMINOPHORE / "THIRD_PARTY_NOTICES.md").read_text()
        self.assertIn('provides=("hyprland=${pkgver}")', package)
        self.assertIn("xdg-desktop-portal-hyprland", notices)
        self.assertIn("shared libraries", notices)

    def test_occupy_output_is_a_bounded_idempotent_compositor_action(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreOccupyOutputController.cpp").read_text()
        header = (ROOT / "src/luminophore/LuminophoreOccupyOutputController.hpp").read_text()
        bindings = (ROOT / "src/config/lua/bindings/LuaBindingsDispatchers.cpp").read_text()
        self.assertIn("MAX_SEEN_REQUESTS = 256", header)
        self.assertIn("requestKey(window, serial)", controller)
        self.assertIn("Fullscreen::controller()->isFullscreen(window)", controller)
        self.assertIn("std::nullopt, true", controller)
        self.assertIn('Internal::setFn(L, "occupy_output", hlLuminophoreOccupyOutput)', bindings)

    def test_luminophore_effect_is_a_typed_same_frame_pass(self) -> None:
        renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        pass_header = (ROOT / "src/render/luminophore/LuminophoreWindowEffectPassElement.hpp").read_text()
        effect = (ROOT / "src/render/luminophore/LuminophoreWindowEffect.cpp").read_text()
        self.assertIn("m_luminophoreWindowEffect->enqueue", renderer)
        self.assertIn("class CLuminophoreWindowEffectPassElement final", pass_header)
        self.assertIn("position.x - monitor->m_position.x", effect)
        self.assertNotIn("getFullWindowBoundingBox", effect)
        self.assertNotIn("geometricBox", effect)
        self.assertIn("renderModif.applyToBox", effect)

    def test_luminophore_effect_preserves_fullscreen_and_floating_contracts(self) -> None:
        renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        effect = (ROOT / "src/render/luminophore/LuminophoreWindowEffect.cpp").read_text()
        enqueue = renderer.index("m_luminophoreWindowEffect->enqueue")
        self.assertIn("showsWindowEffect(pWindow)", renderer[enqueue - 180:enqueue + 120])
        self.assertNotIn("renderdata.decorate &&", renderer[enqueue - 120:enqueue + 120])
        self.assertIn(".renderMask    = !window->m_isFloating", effect)

    def test_luminophore_frame_policy_and_diagnostics_are_separate(self) -> None:
        scheduler = (ROOT / "src/render/luminophore/LuminophoreFrameScheduler.cpp").read_text()
        diagnostics = (ROOT / "src/debug/LuminophoreDiagnostics.cpp").read_text()
        self.assertIn("MONITOR->addDamage(DAMAGE)", scheduler)
        self.assertNotIn("damageWindow(window, true)", scheduler)
        self.assertIn("recordFrameRequest", scheduler)
        self.assertIn("SLuminophoreDiagnosticsSnapshot", diagnostics)

    def test_inner_resize_band_is_independent_from_visual_border(self) -> None:
        values = (ROOT / "src/config/values/ConfigValues.cpp").read_text()
        source = (ROOT / "src/managers/input/InputManager.cpp").read_text()
        header = (ROOT / "src/managers/input/InputManager.hpp").read_text()
        self.assertIn('"general:resize_on_border_inner_area"', values)
        self.assertIn("borderIconDirectionForWindow", header)
        self.assertIn("innerBox = box.copy().expand(-INNERGRAB)", source)
        self.assertIn("borderIconDirectionForWindow(w, mouseCoords) != BORDERICON_NONE", source)
        self.assertEqual(source.count("eBorderIconDirection CInputManager::borderIconDirectionForWindow"), 1)

    def test_shell_bloom_diagnostics_expose_cache_churn(self) -> None:
        control = (ROOT / "src/debug/HyprCtl.cpp").read_text()
        bloom = (ROOT / "src/render/luminophore/LuminophoreShellBloom.cpp").read_text()
        self.assertIn("shellBloom", control)
        self.assertIn("shellAllocations", control)
        self.assertIn("allocation_count", bloom)
        self.assertIn("fallbacks", bloom)

    def test_shell_projection_is_versioned_and_generation_guarded(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        bindings = (ROOT / "src/config/lua/bindings/LuaBindingsDispatchers.cpp").read_text()
        fixture = json.loads((LUMINOPHORE / "tests" / "fixtures" / "shell-projection-v4.json").read_text())
        self.assertEqual(fixture["version"], 4)
        self.assertIn("version != 4", bindings)
        self.assertIn('"revision"', bindings)
        self.assertIn('"content_revision"', bindings)
        self.assertIn('"requested_plane"', bindings)
        self.assertIn('"role"', bindings)
        self.assertIn("revision must be a positive integer", bindings)
        self.assertIn('"prepare"', bindings)
        self.assertIn('"commit"', bindings)
        self.assertIn('"abort"', bindings)
        self.assertIn('surfaceNamespace.starts_with("luminophore-shell-")', controller)
        self.assertIn("revision < latest->second", controller)
        self.assertIn("revision == latest->second", controller)
        self.assertIn("PENDING->second->generation == generation", controller)

    def test_shell_projection_commit_only_replaces_immutable_state(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        header = (ROOT / "src/luminophore/LuminophoreShellProjection.hpp").read_text()
        self.assertIn("std::shared_ptr<const SSnapshot>", header)
        self.assertIn("requestedPlane", header)
        self.assertIn("m_framePlanes", header)
        self.assertNotIn("effectivePlane  =", header)
        self.assertIn("contentRevision", header)
        self.assertIn("m_committed[NAMESPACE]", controller)
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

    def test_shell_projection_fails_closed_when_binding_changes(self) -> None:
        controller = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        self.assertIn("surface->m_mapped", controller)
        self.assertIn("surface->m_namespace == snapshot->surfaceNamespace", controller)
        self.assertIn("surface->m_monitor.lock() == monitor", controller)
        self.assertIn("std::chrono::milliseconds(500)", controller)
        self.assertIn("std::chrono::seconds(1)", controller)

    def test_shell_bloom_is_inserted_immediately_before_shell_content(self) -> None:
        renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        bloom = (ROOT / "src/render/luminophore/LuminophoreShellBloom.cpp").read_text()
        shader = (ROOT / "src/render/luminophore/shaders/LuminophoreShellBloom.hpp").read_text()
        pyramid = (ROOT / "src/render/luminophore/LuminophoreBloomPyramid.hpp").read_text()
        render_layer = renderer.index("void IHyprRenderer::renderLayer")
        enqueue = renderer.index("enqueueBloom", render_layer)
        content = renderer.index("CSurfacePassElement", enqueue)
        self.assertLess(enqueue, content)
        self.assertNotIn("effectiveInputRegion().getExtents()", bloom)
        self.assertIn("globalPanel.copy().translate(-monitor->m_position)", bloom)
        self.assertIn("texture(tex", shader)
        self.assertIn("LUMINOPHORE_BLOOM_LEVEL_COUNT 4", pyramid)
        self.assertIn("luminophore_bloom_prepare_capacity", bloom)
        self.assertIn("luminophore_bloom_generate", bloom)

    def test_shell_bloom_requires_explicit_canary_opt_in(self) -> None:
        projection = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        actions = (ROOT / "src/config/shared/actions/ConfigActions.cpp").read_text()
        self.assertIn("!snapshot->style.bloom", projection)
        self.assertIn("m_bloom->enqueue", projection)
        self.assertIn("#ifndef LUMINOPHORE_EFFECTS", actions)

    def test_shell_projection_commit_is_applied_at_frame_boundary(self) -> None:
        projection = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        transaction = (ROOT / "src/luminophore/LuminophoreMonitorTransaction.cpp").read_text()
        self.assertIn("m_pending[surfaceNamespace] = IT->second", projection)
        self.assertIn("shellProjection()->applyPendingForMonitor(monitor)", transaction)
        begin = renderer.index("Luminophore::monitorTransaction()->beginFrame(pMonitor)")
        scanout = renderer.index("canAttemptDirectScanoutFast", begin)
        self.assertLess(begin, scanout)

    def test_shell_projection_tracks_presentation_and_blocks_scanout(self) -> None:
        projection = (ROOT / "src/luminophore/LuminophoreShellProjection.cpp").read_text()
        monitor = (ROOT / "src/output/Monitor.cpp").read_text()
        transaction = (ROOT / "src/luminophore/LuminophoreMonitorTransaction.cpp").read_text()
        self.assertIn("m_awaitingPresentation", projection)
        self.assertIn('event = "luminophoreshellpresented"', projection)
        self.assertIn("Luminophore::monitorTransaction()->presented(m_self.lock())", monitor)
        self.assertIn("shellProjection()->presentedForMonitor(monitor)", transaction)
        self.assertIn("compositionPolicy()->blocksDirectScanout", monitor)
        self.assertIn("std::chrono::milliseconds(500)", projection)
        self.assertIn("std::chrono::seconds(1)", projection)
        self.assertIn("PENDING->second->revision == revision", projection)


if __name__ == "__main__":
    unittest.main()
