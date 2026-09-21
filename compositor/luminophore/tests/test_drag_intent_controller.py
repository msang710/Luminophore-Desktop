from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "src" / "luminophore" / "LuminophoreDragIntentController.hpp"
SOURCE = ROOT / "src" / "luminophore" / "LuminophoreDragIntentController.cpp"
DRAG = ROOT / "src" / "layout" / "supplementary" / "DragController.cpp"


class DragIntentControllerContractTests(unittest.TestCase):
    def test_retired_drop_candidates_and_native_mutations_are_absent(self):
        source = SOURCE.read_text() + HEADER.read_text() + DRAG.read_text()
        for retired in ("DRAG_INTENT_OCCUPY_OUTPUT", "DRAG_INTENT_MINIMIZE", "DRAG_INTENT_TILE_HALF", "dropTargetAt", "dropTargetIsCurrent", "halfClearFromDrop", "luminophoredragintent"):
            self.assertNotIn(retired, source)
        self.assertNotIn("occupyOutputController()->apply", source)
        self.assertNotIn("windowGestureController()->minimize(", source)

    def test_shake_is_retained_without_shell_or_monitor_dependency(self):
        header = HEADER.read_text()
        self.assertIn("bool", header)
        self.assertIn("shake", header)
        self.assertNotIn("PHLMONITOR", header)
        self.assertNotIn("targetBox", header)
        source = SOURCE.read_text()
        self.assertNotIn("LuminophoreShellProjection", source)
        self.assertNotIn("EventManager", source)
        self.assertEqual(DRAG.read_text().count("m_dragIntentController.begin();"), 1)
        self.assertIn("minimizeOthers", DRAG.read_text())

    def test_directional_edge_does_not_mutate_half_layout(self):
        actions = (ROOT / "src/config/shared/actions/ConfigActions.cpp").read_text()
        gesture = (ROOT / "src/luminophore/LuminophoreWindowGestureController.cpp").read_text()
        self.assertNotIn("halfClearAfterDirectionalBlock", actions + gesture)
        self.assertNotIn("applyHalfClear", gesture)
        self.assertIn("moveInDirection(candidate->layoutTarget(), dirToString(direction))", actions)

    def test_shell_has_no_legacy_drop_surface_or_event_consumer(self):
        shell = ROOT.parent / "config/luminophore_shell"
        source = (shell / "app.py").read_text() + (shell / "ui/surface.py").read_text()
        for retired in ("TopDropSurface", "maximize_targets", "luminophoredragintent", "set_drop_state"):
            self.assertNotIn(retired, source)

    def test_forced_cancel_skips_drop_grouping_shake_and_focus_reacquisition(self) -> None:
        source = DRAG.read_text()
        end = source[source.index("void CDragStateController::dragEnd(bool cancelled)"):source.index("void CDragStateController::mouseMove")]
        self.assertIn("if (!spatialOwned && !cancelled && m_dragMode == MBIND_MOVE && draggingTarget->window())", end)
        self.assertIn("if (!spatialOwned && !cancelled && draggingTarget->window() && DRAG_INTENT.shake)", end)
        self.assertIn("if (!cancelled)\n        Desktop::focusState()->fullWindowFocus", end)
        self.assertIn("finishFloatingMotion(originalTarget)", end)
        manager = (ROOT / "src/layout/LayoutManager.cpp").read_text()
        cancel = manager[manager.index("void CLayoutManager::cancelDragTarget()"):manager.index("void CLayoutManager::endDragTarget()")]
        self.assertIn("dragEnd(true)", cancel)

    def test_forced_callers_cancel_and_expired_target_still_cleans_up(self) -> None:
        keys = (ROOT / "src/managers/KeybindManager.cpp").read_text()
        self.assertIn("ensureMouseBindState(true)", keys)
        self.assertIn("ensureMouseBindState(e.state != WL_POINTER_BUTTON_STATE_RELEASED)", keys)
        focus = (ROOT / "src/desktop/state/FocusState.cpp").read_text()
        self.assertIn("drag && drag->window() != pWindow", focus)
        self.assertIn("g_layoutManager->cancelDragTarget();", focus)
        window = (ROOT / "src/desktop/view/Window.cpp").read_text()
        self.assertIn("g_layoutManager->cancelDragTarget();", window)
        source = DRAG.read_text()
        end = source[source.index("void CDragStateController::dragEnd(bool cancelled)"):source.index("const auto DRAG_INTENT", source.index("void CDragStateController::dragEnd(bool cancelled)"))]
        for cleanup in ("m_target.reset()", "m_dragMode", "MBIND_INVALID", "m_exclusiveDeviceGrab", "m_forcedGrabbedCorner.reset()", "m_dragIntentController.cancel()", "unsetOverride"):
            self.assertIn(cleanup, end)
        self.assertIn("!DRAGGINGTARGET || !validMapped(DRAGGINGTARGET->window())", source)


if __name__ == "__main__":
    unittest.main()
