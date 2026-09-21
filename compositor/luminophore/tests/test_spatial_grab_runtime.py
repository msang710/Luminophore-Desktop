from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/luminophore/LuminophoreSpatialGrabController.cpp"


class SpatialGrabRuntimeTests(unittest.TestCase):
    def test_release_consumes_grab_before_runtime_commit_and_cancel_has_no_command(self):
        source = SOURCE.read_text()
        end = source[source.index("void CLuminophoreSpatialGrabController::end("):source.index("bool CLuminophoreSpatialGrabController::active()")]
        self.assertLess(end.index("m_grab.release("), end.index("spatialRuntime()->edit(*command, false)"))
        self.assertLess(end.index("m_target.reset()"), end.index("spatialRuntime()->edit(*command, false)"))
        self.assertIn("if (!cancelled", end)
        self.assertIn("m_grab.cancel()", end)
        self.assertIn("source.active && source.committed", end)
        self.assertIn("== state.window", end)

    def test_editor_coordinates_use_presented_frame_and_spatial_output_identity(self):
        source = SOURCE.read_text()
        frame = source[source.index("CLuminophoreSpatialGrabController::editorFrame()"):source.index("bool CLuminophoreSpatialGrabController::bindLayout")]
        self.assertIn("reinterpret_cast<uintptr_t>(monitor.get())", frame)
        self.assertNotIn("monitor->m_id", frame)
        self.assertIn("SHELL_SNAPSHOT_PRESENTED", frame)
        self.assertIn('"luminophore-shell-spatial-editor"', frame)
        self.assertIn("!surface->m_mapped", frame)
        self.assertIn("frame->renderBox.x", frame)
        self.assertIn("frame->renderBox.y", frame)
        self.assertIn("frame->opacity < 1.F", frame)

    def test_layout_is_bound_to_grab_model_topology_and_frame_revisions(self):
        source = SOURCE.read_text()
        bind = source[source.index("bool CLuminophoreSpatialGrabController::bindLayout"):source.index("void CLuminophoreSpatialGrabController::update")]
        for guard in ("generation != m_grab.state()->generation", "source.revision != revision", "source.topologyRevision != topologyRevision",
                      "frame->generation != frameGeneration", "frame->revision != frameRevision"):
            self.assertIn(guard, bind)
        self.assertIn("m_grab.bindLayout(generation, grabFacts(source), SLuminophoreEditorFrame{.targetEpoch = targetEpoch}, {})", bind)

    def test_motion_uses_preview_only_and_invalid_target_cancels(self):
        source = SOURCE.read_text()
        motion = source[source.index("void CLuminophoreSpatialGrabController::update"):source.index("void CLuminophoreSpatialGrabController::end")]
        self.assertIn("spatialRuntime()->edit(*command, true)", motion)
        self.assertNotIn("edit(*command, false)", motion)
        self.assertIn("!= state.window", motion)
        self.assertIn("end(true, x, y)", motion)

    def test_event_source_is_compositor_instance_not_grab_counter(self):
        source = SOURCE.read_text()
        publish = source[source.index("void CLuminophoreSpatialGrabController::publish"):source.index("bool CLuminophoreSpatialGrabController::begin")]
        self.assertIn('"source":"{}"', publish)
        self.assertIn("g_pCompositor->m_instanceSignature", publish)
        self.assertIn("escapeJSONStrings", publish)

    def test_native_tiled_grab_bypasses_floating_and_captures_after_focus(self):
        source = (ROOT / "src/layout/supplementary/DragController.cpp").read_text()
        begin = source[source.index("void CDragStateController::dragBegin"):source.index("void CDragStateController::dragEnd")]
        self.assertIn("mode == MBIND_MOVE", begin)
        self.assertIn("HL_MODIFIER_META", begin)
        self.assertIn("managesTiledTarget", begin)
        self.assertIn("managesFloatingTarget", begin)
        self.assertIn("m_exclusiveDeviceGrab || m_spatialGrabOwned", begin)
        self.assertLess(begin.index("if (m_spatialGrabOwned)"), begin.index("else if (updateDragWindow())"))
        self.assertLess(begin.index("rawWindowFocus("), begin.index("spatialGrabController()->begin("))
        motion = source[source.index("void CDragStateController::mouseMove"):]
        branch = motion[motion.index("if (m_spatialGrabOwned)"):motion.index("// Yoink")]
        self.assertIn("spatialGrabController()->update(mousePos.x, mousePos.y)", branch)
        self.assertIn("return;", branch)
        self.assertNotIn("changeFloatingMode", branch)
        self.assertNotIn("->active()", branch)

    def test_native_end_releases_owner_before_commit_and_keeps_floating_finalizer(self):
        source = (ROOT / "src/layout/supplementary/DragController.cpp").read_text()
        end = source[source.index("void CDragStateController::dragEnd"):source.index("void CDragStateController::mouseMove")]
        self.assertLess(end.index("= false", end.index("m_spatialGrabOwned")), end.index("spatialGrabController()->end("))
        self.assertLess(end.index("m_target.reset()"), end.index("spatialGrabController()->end("))
        self.assertIn("cancelled || spatialShake", end)
        self.assertIn("if (spatialOwned)", end)
        self.assertIn("finishFloatingMotion(originalTarget)", end)
        self.assertIn("!spatialOwned && !cancelled", end)
        self.assertIn("minimizeOthers(draggingTarget->window())", end)

    def test_successful_layout_refreshes_preview_for_stationary_pointer(self):
        source = SOURCE.read_text()
        bind = source[source.index("bool CLuminophoreSpatialGrabController::bindLayout"):source.index("void CLuminophoreSpatialGrabController::update")]
        self.assertIn("if (accepted)", bind)
        self.assertIn("update(m_pointerX, m_pointerY)", bind)
        motion = source[source.index("void CLuminophoreSpatialGrabController::update"):source.index("void CLuminophoreSpatialGrabController::end")]
        self.assertIn("m_pointerX = x", motion)
        self.assertIn("m_pointerY = y", motion)

    def test_reserved_updates_are_invalidated_before_release_and_host_checks_precede_cache(self):
        source = SOURCE.read_text()
        motion = source[source.index("void CLuminophoreSpatialGrabController::processUpdate"):source.index("void CLuminophoreSpatialGrabController::end")]
        for check in ("source.revision != state.revision", "source.topologyRevision != state.topologyRevision", "participationFor(target)", "editorFrame()", "m_grab.commandAt"):
            self.assertLess(motion.index(check), motion.index("m_previewCache.matches"))
        end = source[source.index("void CLuminophoreSpatialGrabController::end"):source.index("bool CLuminophoreSpatialGrabController::active")]
        self.assertLess(end.index("m_updateQueue.cancel()"), end.index("m_grab.release"))
        self.assertLess(end.index("m_pendingUpdate.reset()"), end.index("m_grab.release"))
        self.assertNotIn("m_previewCache.matches", end)
        self.assertIn("lifetime.lock()", source)
