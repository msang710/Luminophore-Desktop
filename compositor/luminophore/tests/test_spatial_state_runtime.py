from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def function_body(source: str, signature: str, next_signature: str) -> str:
    return source[source.index(signature) : source.index(next_signature, source.index(signature))]


class SpatialStateRuntimeTests(unittest.TestCase):
    def test_full_window_scan_is_bootstrap_only(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        bootstrap = function_body(source, "void CLuminophoreSpatialRuntime::bootstrap()", "void CLuminophoreSpatialRuntime::observeTarget")

        self.assertEqual(source.count("Desktop::windowState()->windows()"), 1)
        self.assertIn("Desktop::windowState()->windows()", bootstrap)
        self.assertIn("if (m_bootstrapped)", bootstrap)
        self.assertNotIn("synchronizeWindows", source)
        self.assertNotIn("reconcile()", source)

    def test_snapshot_is_read_only(self) -> None:
        header = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.hpp").read_text()
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        snapshot = function_body(source, "SSpatialSnapshot CLuminophoreSpatialRuntime::snapshot() const", "SLuminophoreSpatialTransactionResult CLuminophoreSpatialRuntime::submit")

        self.assertIn("snapshot() const;", header)
        self.assertIn("m_model->snapshot()", snapshot)
        self.assertIn("m_committer.presented()", snapshot)
        self.assertIn("committedModelRevision", header)
        self.assertIn("committedTopologyRevision", header)
        self.assertIn("committedBox", header)
        for forbidden in ("bootstrap(", "observeTarget(", "submit(", "transact(", "physicalOutputs("):
            self.assertNotIn(forbidden, snapshot)

    def test_target_lifecycle_is_the_membership_producer(self) -> None:
        space = (ROOT / "src/layout/space/Space.cpp").read_text()
        target = (ROOT / "src/layout/target/Target.cpp").read_text()

        self.assertEqual(target.count("observeTarget(m_self.lock());"), 1)
        self.assertEqual(space.count("observeTarget(t, desiredFloatingBox);"), 1)
        self.assertNotIn("reconcile()", space)
        self.assertIn("topologyChanged();", space)

    def test_observe_target_emits_one_atomic_membership_command(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        observe = function_body(source, "void CLuminophoreSpatialRuntime::observeTarget", "void CLuminophoreSpatialRuntime::topologyChanged")

        self.assertIn("SObserveWindowCommand", observe)
        self.assertNotIn("SAddTiledCommand", observe)
        self.assertNotIn("SRemoveWindowCommand", observe)
        self.assertNotIn("applyProjection", observe)

    def test_user_actions_emit_typed_commands(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        dispatch = function_body(source, "bool CLuminophoreSpatialRuntime::dispatch", "bool CLuminophoreSpatialRuntime::active")

        self.assertIn("SMoveViewCommand", dispatch)
        self.assertIn("SAdjustViewCommand", dispatch)
        self.assertIn(".anchorKey                = key", dispatch)
        self.assertIn("SMoveTiledCommand", dispatch)
        self.assertIn("expectedTopologyRevision = m_topologyRevision", dispatch)
        self.assertIn("selectedOutputID(window)", dispatch)
        self.assertNotIn("anchorKeyForOutput", dispatch)
        self.assertNotIn("applyProjection", dispatch)

    def test_editor_preview_preserves_external_revision_and_skips_publication(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        edit = function_body(source, "SLuminophoreSpatialTransactionResult CLuminophoreSpatialRuntime::edit", "bool CLuminophoreSpatialRuntime::desktopExposed")
        self.assertIn("Luminophore::applyEditorCandidate(*candidate, command)", edit)
        candidate = (ROOT / "src/luminophore/LuminophoreSpatialEdit.cpp").read_text()
        self.assertIn("return candidate.transact(command)", candidate)
        self.assertNotIn("enqueue(", edit)
        self.assertNotIn("expectedRevision =", edit)
        self.assertLess(edit.index("if (previewOnly)"), edit.index("m_windows.takeMotion"))
        self.assertLess(edit.index("if (previewOnly)"), edit.index("commitSnapshot(result.snapshot)"))
        self.assertLess(edit.index("commitSnapshot(result.snapshot)"), edit.index("m_model = std::move(candidate)"))
        self.assertIn("m_windows.activeMotion", edit)
        self.assertIn("m_windows.restoreMotion(*movedKey, *savedMotion)", edit)
        hyprctl = (ROOT / "src/debug/HyprCtl.cpp").read_text()
        self.assertIn('SHyprCtlCommand{"luminophorespatialpreview", false, luminophoreSpatialPreviewRequest}', hyprctl)
        self.assertIn("spatialRuntime()->edit(*command, true)", hyprctl)

    def test_desktop_gates_all_clients_and_preserves_shell_pointer_focus(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        window = (ROOT / "src/desktop/view/Window.cpp").read_text()
        focus = (ROOT / "src/desktop/state/FocusState.cpp").read_text()
        self.assertIn("effective->presentationMode == eLuminophorePresentationMode::DESKTOP", source)
        self.assertIn("m_spatiallySuppressed || Luminophore::spatialRuntime()->desktopExposed()", window)
        self.assertIn("m_inputBlockReasons != INPUT_BLOCK_NONE || Luminophore::spatialRuntime()->desktopExposed()", window)
        self.assertIn("pWindow && Luminophore::spatialRuntime()->desktopExposed()", focus)
        self.assertIn("m_desktopFocus = previousFocus", source)
        native = (ROOT / "src/luminophore/LuminophoreSpatialNativeAdapter.cpp").read_text()
        self.assertIn("SpatialNative::clearClientPointerFocus()", source)
        self.assertIn("VIEW_TYPE_WINDOW", native)
        self.assertIn("setPointerFocus(nullptr, {})", native)
        self.assertIn("damageMonitor(monitor)", native)

    def test_directional_focus_is_wired_through_typed_dispatch_and_result_event(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        dispatch = function_body(source, "bool CLuminophoreSpatialRuntime::dispatch", "bool CLuminophoreSpatialRuntime::active")
        actions = (ROOT / "src/config/shared/actions/ConfigActions.cpp").read_text()
        lua = (ROOT / "src/config/lua/bindings/LuaBindingsDispatchers.cpp").read_text()
        self.assertIn("SFocusDirectionCommand", dispatch)
        self.assertIn("presentation->primaryOutputID", dispatch)
        events = (ROOT / "src/luminophore/LuminophoreSpatialEvents.cpp").read_text()
        self.assertIn("SpatialEvents::action(", dispatch)
        self.assertIn('"luminophorespatialaction"', events)
        self.assertIn("pointJSON(before, eventKey)", events)
        self.assertIn("pointJSON(result.snapshot, key)", events)
        self.assertIn("return emit(result, reason)", dispatch)
        self.assertIn("Actions::spatialFocusDirection", actions)
        self.assertIn('"focus_direction", hlLuminophoreFocusDirection', lua)

    def test_view_target_uses_cursor_and_off_view_does_not_clear_seat_focus(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        selection = (ROOT / "src/luminophore/LuminophoreSpatialTopology.cpp").read_text()
        self.assertIn("SpatialTopology::selectedOutputID(m_model->outputViews())", source)
        self.assertIn("Pointer::mgr()->position()", selection)
        self.assertIn("logicalBox().containsPoint(position)", selection)
        self.assertNotIn("focusState()", selection)
        window = (ROOT / "src/desktop/view/Window.cpp").read_text()
        self.assertIn("inputBlockRevokesFocus(m_inputBlockReasons, Luminophore::spatialRuntime()->desktopExposed())", window)
        self.assertIn("remainingReasons & ~sc<uint32_t>(INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW)", window)

    def test_floating_motion_uses_physical_override_until_release(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        update = function_body(source, "bool CLuminophoreSpatialRuntime::updateFloatingMotion", "void CLuminophoreSpatialRuntime::finishFloatingMotion")
        self.assertNotIn("submit(", update)
        finish = function_body(source, "void CLuminophoreSpatialRuntime::finishFloatingMotion", "bool CLuminophoreSpatialRuntime::setFloatingGeometry")
        self.assertEqual(finish.count("setFloatingGeometry("), 1)
        self.assertIn("m_windows.restoreMotion(key, box)", finish)
        geometry = function_body(source, "bool CLuminophoreSpatialRuntime::setFloatingGeometry", "bool CLuminophoreSpatialRuntime::managesFloatingTarget")
        self.assertIn("projectedCells()", geometry)
        self.assertNotIn("m_model->view()", geometry)
        drag = (ROOT / "src/layout/supplementary/DragController.cpp").read_text()
        self.assertIn("beginFloatingMotion(DRAGGINGTARGET)", drag)
        self.assertIn("CScopeGuard finishFloating", drag)
        self.assertIn("finishFloatingMotion(originalTarget)", drag)
        target = (ROOT / "src/layout/target/WindowTarget.cpp").read_text()
        self.assertIn("updateFloatingMotion(m_self.lock(), box.logicalBox)", target)

    def test_candidate_model_is_published_only_after_batch_commit(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        submit = function_body(source, "SLuminophoreSpatialTransactionResult CLuminophoreSpatialRuntime::submit", "void CLuminophoreSpatialRuntime::applyFocusUpdate")
        commit = function_body(source, "bool CLuminophoreSpatialRuntime::commitSnapshot", "bool CLuminophoreSpatialRuntime::dispatch")

        self.assertIn("makeUnique<CLuminophoreSpatialModel>(*m_model)", submit)
        self.assertLess(submit.index("commitSnapshot(candidate->snapshot())"), submit.index("std::move(candidate)"))
        self.assertIn("eLuminophoreSpatialTransactionStatus::COMMIT_FAILED", submit)
        self.assertIn("m_committer.applyBatch", commit)
        self.assertLess(commit.index("CResolvedBatch::prepare"), commit.index("m_committer.applyBatch"))
        native = (ROOT / "src/luminophore/LuminophoreSpatialNativeAdapter.cpp").read_text()
        self.assertIn("SResolvedTarget", native)
        self.assertIn("mode == eLuminophorePresentationMode::WIDE", native)

    def test_topology_is_one_model_command_with_output_identity(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        topology = function_body(source, "void CLuminophoreSpatialRuntime::topologyChanged", "bool CLuminophoreSpatialRuntime::commitCurrent")

        self.assertIn("SSpatialTopologyCommand", topology)
        self.assertIn("o.id, o.name", topology)
        self.assertNotIn("SReconfigureExtentCommand", topology)

    def test_queue_serializes_against_current_revision(self) -> None:
        header = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.hpp").read_text()
        queue_header = (ROOT / "src/luminophore/LuminophoreSpatialCommandQueue.hpp").read_text()
        queue_source = (ROOT / "src/luminophore/LuminophoreSpatialCommandQueue.cpp").read_text()

        self.assertIn("CLuminophoreSpatialCommandQueue", header)
        self.assertIn("std::deque<LuminophoreSpatialPayload>", queue_header)
        self.assertIn("expectedRevision = model.revision()", queue_source)
        self.assertIn("while (!m_pending.empty())", queue_source)

    def test_pr3_runtime_has_one_committer_geometry_writer(self) -> None:
        source = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        committer = (ROOT / "src/luminophore/LuminophoreSpatialCommitter.cpp").read_text()

        native = (ROOT / "src/luminophore/LuminophoreSpatialNativeAdapter.cpp").read_text()
        self.assertNotIn("target->setPositionGlobal", source)
        self.assertEqual(native.count("target->setPositionGlobal"), 1)
        self.assertNotIn("applyBatch(", native)
        self.assertEqual(source.count("m_committer.applyBatch"), 1)
        self.assertIn("CLuminophoreSpatialCommitter::prepare", source)
        self.assertIn("targetExists(entry.key)", committer)
        self.assertLess(committer.index("targetExists(entry.key)"), committer.index("writer(commit.entries)"))
        # Runtime owns the single geometry writer.  Renderer damage around that
        # writer is intentional: it invalidates both the previous presentation
        # and the staged fragment presentation without becoming a second
        # geometry/projection authority.
        self.assertEqual(native.count("g_pHyprRenderer->damageWindow(window, true)"), 1)
        for forbidden in ("assignToSpace", "applyProjection"):
            self.assertNotIn(forbidden, source)

    def test_base_tiled_targets_never_enter_legacy_geometry_algorithm(self) -> None:
        space = (ROOT / "src/layout/space/Space.cpp").read_text()
        target = (ROOT / "src/layout/target/WindowTarget.cpp").read_text()

        self.assertIn("usesLuminophoreSpatialGeometry() && !t->floating()", space)
        self.assertIn("trackSpatialTiledTarget", space)
        self.assertIn("untrackSpatialTiledTarget", space)
        self.assertNotIn("recalculateFloating(reason)", space)
        self.assertIn("usesLuminophoreSpatialGeometry() && target && !target->floating()", space)
        self.assertIn("!CLuminophoreSpatialCommitter::isApplying()", target)

        for legacy_path in (
            "src/layout/supplementary/WorkspaceAlgoMatcher.cpp",
            "src/layout/algorithm/tiled/dwindle/DwindleAlgorithm.cpp",
            "src/layout/algorithm/tiled/master/MasterAlgorithm.cpp",
            "src/layout/algorithm/tiled/monocle/MonocleAlgorithm.cpp",
            "src/layout/algorithm/tiled/scrolling/ScrollingAlgorithm.cpp",
        ):
            self.assertFalse((ROOT / legacy_path).exists(), legacy_path)

    def test_pr4_floating_and_fullscreen_use_spatial_committer(self) -> None:
        runtime = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()
        target = (ROOT / "src/layout/target/WindowTarget.cpp").read_text()
        self.assertFalse((ROOT / "src/layout/target/WindowGroupTarget.cpp").exists())
        fullscreen = (ROOT / "src/managers/fullscreen/FullscreenController.cpp").read_text()
        writer = function_body(target, "void CWindowTarget::setPositionGlobal", "void CWindowTarget::updatePos")

        self.assertIn("SUpdateFloatingCommand", runtime)
        self.assertIn("CLuminophoreSpatialProjection::normalize", runtime)
        self.assertIn("WINDOW_PRESENTATION_OCCUPY_OUTPUT", (ROOT / "src/luminophore/LuminophoreSpatialNativeAdapter.cpp").read_text())
        self.assertIn("spatialRuntime()->setFloatingGeometry", target)
        self.assertIn("spatialRuntime()->commitCurrent();", fullscreen)
        self.assertIn("Blocked protocol fullscreen geometry write", writer)
        self.assertIn("desiredFloatingBox", runtime)
        self.assertNotIn("addTarget(target);", function_body((ROOT / "src/layout/algorithm/Algorithm.cpp").read_text(), "void CAlgorithm::setFloatingForSpatialTarget", "void CAlgorithm::moveTarget"))

    def test_pr5_all_workspaces_construct_only_the_spatial_tiled_adapter(self) -> None:
        workspace = (ROOT / "src/desktop/Workspace.cpp").read_text()
        adapter = (ROOT / "src/luminophore/LuminophoreSpatialTiledAlgorithm.cpp").read_text()
        init = function_body(workspace, "void CWorkspace::init", "CWorkspace::~CWorkspace")

        self.assertIn("CLuminophoreSpatialTiledAlgorithm", init)
        self.assertNotIn("WorkspaceAlgoMatcher", workspace)
        self.assertIn("eSpatialAction::MOVE_WINDOW", adapter)
        self.assertIn("spatialRuntime()->commitCurrent()", adapter)
        self.assertNotIn("setPositionGlobal", adapter)
        for legacy in ("CDwindleAlgorithm", "CMasterAlgorithm", "CScrollingAlgorithm", "CMonocleAlgorithm"):
            self.assertNotIn(legacy, init)

    def test_special_roles_are_external_but_parented_toplevels_own_board_cells(self) -> None:
        runtime = (ROOT / "src/luminophore/LuminophoreSpatialRuntime.cpp").read_text()

        registry = (ROOT / "src/luminophore/LuminophoreSpatialWindowRegistry.cpp").read_text()
        self.assertNotIn("WORKSPACE_ROLE_SCRATCHPAD", registry)
        self.assertIn("role == WORKSPACE_ROLE_SERVICE", registry)
        self.assertIn("window->parent()", registry)
        self.assertIn("window->isModal()", registry)
        classification = function_body(registry, "eSpatialParticipation CLuminophoreSpatialWindowRegistry::classifyParticipation", "eSpatialParticipation CLuminophoreSpatialWindowRegistry::participationFor")
        self.assertNotIn("facts.hasParent", classification)
        self.assertNotIn("facts.modal", classification)
        self.assertIn("return eSpatialParticipation::BOARD_ROOT", classification)
        self.assertIn("eSpatialParticipation::EXTERNAL_OVERLAY", registry)
        self.assertIn("participationFor(target) != eSpatialParticipation::BOARD_ROOT", runtime)

    def test_committed_fragments_are_the_shared_render_damage_input_authority(self) -> None:
        renderer = (ROOT / "src/render/Renderer.cpp").read_text()
        hit_test = (ROOT / "src/desktop/state/ViewHitTester.cpp").read_text()
        policy = (ROOT / "src/luminophore/LuminophoreCompositionPolicy.cpp").read_text()
        per_monitor = function_body(renderer, "bool IHyprRenderer::shouldRenderWindow(PHLWINDOW pWindow, PHLMONITOR pMonitor)",
                                    "bool IHyprRenderer::shouldRenderWindow(PHLWINDOW pWindow)")
        damage = function_body(renderer, "void IHyprRenderer::damageWindow", "void IHyprRenderer::damageMonitor")

        self.assertLess(per_monitor.index("regionsFor(pWindow, pMonitor)"), per_monitor.index("visibleOnMonitor(pMonitor)"))
        self.assertIn("regionsFor(pWindow, m)", damage)
        self.assertIn("spatialHitBox", hit_test)
        self.assertIn("spatialAcceptsPoint", hit_test)
        self.assertIn("spatialRuntime()->desktopExposed()", policy)

    def test_hyprctl_keeps_read_only_spatial_state_query(self) -> None:
        source = (ROOT / "src/debug/HyprCtl.cpp").read_text()

        self.assertIn("luminophoreSpatialStateRequest", source)
        self.assertIn('SHyprCtlCommand{"luminophorespatialstate"', source)
        self.assertIn('\\"extent\\"', source)
        self.assertIn('\\"view\\"', source)
        self.assertIn('\\"windows\\"', source)
        self.assertIn('\\"committed\\"', source)
        self.assertIn('\\"topologyRevision\\"', source)
        self.assertIn('\\"committedRevision\\"', source)
        self.assertIn('\\"presentationMode\\"', source)
        self.assertIn('\\"outputViews\\"', source)
        self.assertIn('\\"wideKey\\"', source)
        self.assertIn('\\"focusedKey\\"', source)
        self.assertIn('\\"box\\"', source)


if __name__ == "__main__":
    unittest.main()
