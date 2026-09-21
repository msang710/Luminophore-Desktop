#include "DragController.hpp"

#include "../LayoutManager.hpp"
#include "../space/Space.hpp"

#include "../../Compositor.hpp"
#include "../../luminophore/LuminophoreWindowGestureController.hpp"
#include "../../luminophore/LuminophoreSpatialRuntime.hpp"
#include "../../luminophore/LuminophoreSpatialGrabController.hpp"
#include "../../devices/IKeyboard.hpp"
#include <hyprutils/utils/ScopeGuard.hpp>
#include "../../managers/SeatManager.hpp"
#include "../../pointer/cursor/CursorShapeOverrideController.hpp"
#include "../../managers/fullscreen/FullscreenController.hpp"
#include "../../desktop/state/FocusState.hpp"
#include "../../desktop/state/WindowState.hpp"
#include "../../protocols/XDGShell.hpp"
#include "../../render/Renderer.hpp"
#include "../../state/MonitorState.hpp"

#include <format>
#include <string_view>

using namespace Layout;
using namespace Layout::Supplementary;

static bool isResizeMode(eMouseBindMode mode) {
    return mode == MBIND_RESIZE || mode == MBIND_RESIZE_FORCE_RATIO || mode == MBIND_RESIZE_BLOCK_RATIO;
}

static std::string_view cursorForResizeEdge(eRectCorner edge) {
    if (edgeTop(edge) && edgeLeft(edge))
        return "nw-resize";
    if (edgeTop(edge) && edgeRight(edge))
        return "ne-resize";
    if (edgeBottom(edge) && edgeRight(edge))
        return "se-resize";
    if (edgeBottom(edge) && edgeLeft(edge))
        return "sw-resize";
    if (edgeTop(edge))
        return "n-resize";
    if (edgeBottom(edge))
        return "s-resize";
    if (edgeLeft(edge))
        return "w-resize";
    if (edgeRight(edge))
        return "e-resize";

    return "se-resize";
}

static void setXDGResizingState(SP<ITarget> target, bool resizing) {
    if (!target || !target->window() || target->window()->m_isX11 || !target->window()->m_xdgSurface || !target->window()->m_xdgSurface->m_toplevel)
        return;

    target->window()->m_xdgSurface->m_toplevel->setResizing(resizing);
}

SP<ITarget> CDragStateController::target() const {
    return m_target.lock();
}

eMouseBindMode CDragStateController::mode() const {
    return m_dragMode;
}

bool CDragStateController::wasDraggingWindow() const {
    return m_wasDraggingWindow;
}

bool CDragStateController::dragThresholdReached() const {
    return m_dragThresholdReached;
}

void CDragStateController::resetDragThresholdReached() {
    m_dragThresholdReached = false;
}

bool CDragStateController::draggingTiled() const {
    return m_draggingTiled;
}

bool CDragStateController::exclusiveDeviceGrab() const {
    return m_exclusiveDeviceGrab && !m_target.expired();
}

bool CDragStateController::updateDragWindow() {
    const auto DRAGGINGTARGET = m_target.lock();
    const bool WAS_FULLSCREEN = DRAGGINGTARGET->window() ? Fullscreen::controller()->isFullscreen(DRAGGINGTARGET->window()) : false;

    if (m_dragThresholdReached) {
        if (WAS_FULLSCREEN) {
            Log::logger->log(Log::DEBUG, "Dragging a fullscreen window");
            Fullscreen::controller()->setFullscreenMode(DRAGGINGTARGET->window(), Fullscreen::FSMODE_NONE);
        }

        const auto PWORKSPACE     = DRAGGINGTARGET->workspace();
        const auto DRAGGINGWINDOW = DRAGGINGTARGET->window();

        if (Fullscreen::controller()->hasFullscreen(PWORKSPACE) && (!DRAGGINGTARGET->floating() || !DRAGGINGWINDOW->isAllowedOverFullscreen())) {
            Log::logger->log(Log::DEBUG, "Rejecting drag on a fullscreen workspace. (window under fullscreen)");
            g_layoutManager->cancelDragTarget();
            return true;
        }
    }

    m_draggingTiled                   = false;
    m_draggingWindowOriginalFloatSize = DRAGGINGTARGET->lastFloatingSize();

    if (WAS_FULLSCREEN && DRAGGINGTARGET->floating() && m_dragThresholdReached) {
        const auto MOUSECOORDS = g_pInputManager->getMouseCoordsInternal();
        DRAGGINGTARGET->setPositionGlobal(CBox{MOUSECOORDS - DRAGGINGTARGET->position().size() / 2.F, DRAGGINGTARGET->position().size()});
    } else if (!DRAGGINGTARGET->floating() && m_dragMode == MBIND_MOVE) {
        Vector2D MINSIZE = DRAGGINGTARGET->minSize().value_or(Vector2D{MIN_WINDOW_SIZE, MIN_WINDOW_SIZE});
        DRAGGINGTARGET->rememberFloatingSize((DRAGGINGTARGET->position().size() * 0.8489).clamp(MINSIZE, Vector2D{}).floor());

        if (m_dragThresholdReached) {
            DRAGGINGTARGET->setPositionGlobal(CBox{g_pInputManager->getMouseCoordsInternal() - DRAGGINGTARGET->position().size() / 2.F, DRAGGINGTARGET->position().size()});
            g_layoutManager->changeFloatingMode(DRAGGINGTARGET);
            m_draggingTiled = true;
        }
    }

    const auto DRAG_ORIGINAL_BOX = DRAGGINGTARGET->position();

    m_beginDragXY         = g_pInputManager->getMouseCoordsInternal();
    m_beginDragPositionXY = DRAG_ORIGINAL_BOX.pos();
    m_beginDragSizeXY     = DRAG_ORIGINAL_BOX.size();
    m_lastDragXY          = m_beginDragXY;

    return false;
}

void CDragStateController::dragBegin(SP<ITarget> target, eMouseBindMode mode, std::optional<eRectCorner> forcedEdge, bool exclusiveDeviceGrab, bool fromEditor) {
    if (m_spatialGrabOwned)
        dragEnd(true);
    Luminophore::spatialRuntime()->resizeGesture(isResizeMode(mode));
    m_spatialGrabOwned    = false;
    m_target              = target;
    m_dragMode            = mode;
    m_forcedGrabbedCorner = forcedEdge;
    m_exclusiveDeviceGrab = exclusiveDeviceGrab;
    m_grabbedCorner       = CORNER_NONE;

    const auto  DRAGGINGTARGET = m_target.lock();
    static auto PDRAGTHRESHOLD = CConfigValue<Config::INTEGER>("binds:drag_threshold");

    m_mouseMoveEventCount = 1;
    m_beginDragSizeXY     = Vector2D();

    // Window will be floating. Let's check if it's valid. It should be, but I don't like crashing.
    if (!DRAGGINGTARGET || !validMapped(DRAGGINGTARGET->window())) {
        Log::logger->log(Log::ERR, "Dragging attempted on an invalid window (not mapped)");
        g_layoutManager->cancelDragTarget();
        return;
    }

    if (!DRAGGINGTARGET->workspace()) {
        Log::logger->log(Log::ERR, "Dragging attempted on an invalid window (no workspace)");
        g_layoutManager->cancelDragTarget();
        return;
    }

    Luminophore::spatialRuntime()->beginAuxiliaryGesture(DRAGGINGTARGET);
    m_spatialGrabOwned =
        mode == MBIND_MOVE && (Luminophore::spatialRuntime()->managesTiledTarget(DRAGGINGTARGET) || Luminophore::spatialRuntime()->managesFloatingTarget(DRAGGINGTARGET));
    m_exclusiveDeviceGrab = m_exclusiveDeviceGrab || m_spatialGrabOwned || (isResizeMode(mode) && Luminophore::spatialRuntime()->managesTiledTarget(DRAGGINGTARGET));
    if (!m_spatialGrabOwned)
        Luminophore::spatialRuntime()->beginFloatingMotion(DRAGGINGTARGET);

    // Try to pick up dragged window now if drag_threshold is disabled
    // or at least update dragging related variables for the cursors
    m_dragThresholdReached = *PDRAGTHRESHOLD <= 0;
    if (m_spatialGrabOwned) {
        // Spatial grabs modify presentation only, including floating windows.
        m_draggingTiled       = false;
        m_beginDragXY         = g_pInputManager->getMouseCoordsInternal();
        m_lastDragXY          = m_beginDragXY;
        m_beginDragPositionXY = DRAGGINGTARGET->position().pos();
        m_beginDragSizeXY     = DRAGGINGTARGET->position().size();
    } else if (updateDragWindow())
        return;

    m_dragIntentController.begin();

    // get the grab corner
    static auto RESIZECORNER = CConfigValue<Config::INTEGER>("general:resize_corner");
    if (m_forcedGrabbedCorner && *m_forcedGrabbedCorner != CORNER_NONE) {
        m_grabbedCorner = *m_forcedGrabbedCorner;
        Pointer::Cursor::overrideController->setOverride(std::string{cursorForResizeEdge(m_grabbedCorner)}, Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
    } else if (*RESIZECORNER != 0 && *RESIZECORNER <= 4 && DRAGGINGTARGET->floating()) {
        switch (*RESIZECORNER) {
            case 1:
                m_grabbedCorner = CORNER_TOPLEFT;
                Pointer::Cursor::overrideController->setOverride("nw-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
                break;
            case 2:
                m_grabbedCorner = CORNER_TOPRIGHT;
                Pointer::Cursor::overrideController->setOverride("ne-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
                break;
            case 3:
                m_grabbedCorner = CORNER_BOTTOMRIGHT;
                Pointer::Cursor::overrideController->setOverride("se-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
                break;
            case 4:
                m_grabbedCorner = CORNER_BOTTOMLEFT;
                Pointer::Cursor::overrideController->setOverride("sw-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
                break;
        }
    } else if (m_beginDragXY.x < m_beginDragPositionXY.x + m_beginDragSizeXY.x / 2.F) {
        if (m_beginDragXY.y < m_beginDragPositionXY.y + m_beginDragSizeXY.y / 2.F) {
            m_grabbedCorner = CORNER_TOPLEFT;
            Pointer::Cursor::overrideController->setOverride("nw-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
        } else {
            m_grabbedCorner = CORNER_BOTTOMLEFT;
            Pointer::Cursor::overrideController->setOverride("sw-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
        }
    } else {
        if (m_beginDragXY.y < m_beginDragPositionXY.y + m_beginDragSizeXY.y / 2.F) {
            m_grabbedCorner = CORNER_TOPRIGHT;
            Pointer::Cursor::overrideController->setOverride("ne-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
        } else {
            m_grabbedCorner = CORNER_BOTTOMRIGHT;
            Pointer::Cursor::overrideController->setOverride("se-resize", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
        }
    }

    if (!isResizeMode(m_dragMode))
        Pointer::Cursor::overrideController->setOverride("grabbing", Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);

    if (m_exclusiveDeviceGrab)
        g_pSeatManager->setPointerFocus(nullptr, {});

    DRAGGINGTARGET->damageEntire();

    g_pKeybindManager->shadowKeybinds();

    if (DRAGGINGTARGET->window()) {
        Desktop::focusState()->rawWindowFocus(DRAGGINGTARGET->window(), Desktop::FOCUS_REASON_DESKTOP_STATE_CHANGE);
        Desktop::windowState()->raise(DRAGGINGTARGET->window());
    }

    // Capture revisions after focus has settled; focus may itself publish state.
    if (m_spatialGrabOwned &&
        !Luminophore::spatialGrabController()->begin(DRAGGINGTARGET, m_beginDragXY.x, m_beginDragXY.y,
                                                     SLuminophoreDragOptions{fromEditor ? eLuminophoreDragOrigin::EDITOR : eLuminophoreDragOrigin::WINDOW,
                                                                             fromEditor || (g_pInputManager->getModsFromAllKBs() & HL_MODIFIER_META) ?
                                                                                 eLuminophoreDragPresentation::BADGE :
                                                                                 eLuminophoreDragPresentation::WINDOW})) {
        g_layoutManager->cancelDragTarget();
        return;
    }
    if (isResizeMode(m_dragMode))
        setXDGResizingState(DRAGGINGTARGET, true);
}
void CDragStateController::dragEnd(bool cancelled) {
    Hyprutils::Utils::CScopeGuard finishAuxiliary([cancelled] { Luminophore::spatialRuntime()->endAuxiliaryGesture(cancelled); });
    Hyprutils::Utils::CScopeGuard finishResize([cancelled] { Luminophore::spatialRuntime()->resizeGesture(false, cancelled); });
    auto                          draggingTarget = m_target.lock();
    const auto                    originalTarget = draggingTarget;
    const bool                    spatialOwned   = m_spatialGrabOwned;
    const bool                    spatialShake   = spatialOwned && !cancelled && Luminophore::spatialGrabController()->active() && m_dragIntentController.snapshot().shake;
    m_spatialGrabOwned                           = false;
    if (spatialOwned) {
        // Release native ownership before a spatial commit can re-enter focus/unmap.
        m_target.reset();
        const auto pointer = g_pInputManager->getMouseCoordsInternal();
        Luminophore::spatialGrabController()->end(cancelled || spatialShake, pointer.x, pointer.y, true);
        if (spatialShake && draggingTarget && validMapped(draggingTarget->window()))
            Luminophore::windowGestureController()->minimizeOthers(draggingTarget->window());
    }
    if (spatialOwned) {
        m_dragMode            = MBIND_INVALID;
        m_exclusiveDeviceGrab = false;
        m_draggingTiled       = false;
        m_wasDraggingWindow   = false;
        m_forcedGrabbedCorner.reset();
        m_dragIntentController.cancel();
        Pointer::Cursor::overrideController->unsetOverride(Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
        if (draggingTarget)
            draggingTarget->damageEntire();
        Luminophore::spatialGrabController()->finishEnd();
        return;
    }
    Hyprutils::Utils::CScopeGuard finishFloating([originalTarget] { Luminophore::spatialRuntime()->finishFloatingMotion(originalTarget); });
    if (!draggingTarget) {
        m_target.reset();
        m_dragMode            = MBIND_INVALID;
        m_exclusiveDeviceGrab = false;
        m_forcedGrabbedCorner.reset();
        m_dragIntentController.cancel();
        Pointer::Cursor::overrideController->unsetOverride(Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
        return;
    }
    const auto DRAG_INTENT = m_dragIntentController.snapshot();

    m_mouseMoveEventCount = 1;

    if (!validMapped(draggingTarget->window())) {
        if (draggingTarget->window()) {
            Pointer::Cursor::overrideController->unsetOverride(Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
            m_target.reset();
        }
        m_dragMode            = MBIND_INVALID;
        m_exclusiveDeviceGrab = false;
        m_forcedGrabbedCorner.reset();
        m_dragIntentController.cancel();
        return;
    }

    Pointer::Cursor::overrideController->unsetOverride(Pointer::Cursor::CURSOR_OVERRIDE_SPECIAL_ACTION);
    m_target.reset();
    m_wasDraggingWindow = true;

    if (!spatialOwned && !cancelled && m_dragMode == MBIND_MOVE && draggingTarget->window()) {
        draggingTarget->damageEntire();

        const auto DRAGGING_WINDOW = draggingTarget->window();

        const auto MOUSECOORDS = g_pInputManager->getMouseCoordsInternal();
        PHLWINDOW  pWindow =
            Desktop::viewState()->hitTest().windowAt(MOUSECOORDS, Desktop::View::RESERVED_EXTENTS | Desktop::View::INPUT_EXTENTS | Desktop::View::ALLOW_FLOATING, DRAGGING_WINDOW);

        if (pWindow) {
            if (pWindow->checkInputOnDecos(INPUT_TYPE_DRAG_END, MOUSECOORDS, DRAGGING_WINDOW)) {
                m_wasDraggingWindow   = false;
                m_dragMode            = MBIND_INVALID;
                m_exclusiveDeviceGrab = false;
                m_forcedGrabbedCorner.reset();
                m_dragIntentController.cancel();
                return;
            }
        }
    }

    if (const auto W = draggingTarget->window(); W) {
        W->resetMotionBlur();
        W->m_floatingOffset = {};
    }

    if (m_draggingTiled) {
        // A drop handler may already have restored tiling.
        if (draggingTarget->floating())
            g_layoutManager->changeFloatingMode(draggingTarget);

        draggingTarget->rememberFloatingSize(m_draggingWindowOriginalFloatSize);
    }

    draggingTarget->damageEntire();

    g_layoutManager->setTargetGeom(draggingTarget->position(), draggingTarget);

    if (!cancelled)
        Desktop::focusState()->fullWindowFocus(draggingTarget->window(), Desktop::FOCUS_REASON_DESKTOP_STATE_CHANGE);

    m_wasDraggingWindow = false;
    if (isResizeMode(m_dragMode))
        setXDGResizingState(draggingTarget, false);

    if (!spatialOwned && !cancelled && draggingTarget->window() && DRAG_INTENT.shake)
        Luminophore::windowGestureController()->minimizeOthers(draggingTarget->window());

    m_dragMode            = MBIND_INVALID;
    m_exclusiveDeviceGrab = false;
    m_forcedGrabbedCorner.reset();
    m_dragIntentController.cancel();
}

void CDragStateController::mouseMove(const Vector2D& mousePos) {
    if (m_target.expired()) {
        if (m_dragMode != MBIND_INVALID)
            g_layoutManager->cancelDragTarget();
        return;
    }

    const auto  DRAGGINGTARGET = m_target.lock();
    static auto PDRAGTHRESHOLD = CConfigValue<Config::INTEGER>("binds:drag_threshold");

    // Window invalid or drag begin size 0,0 meaning we rejected it.
    if ((!validMapped(DRAGGINGTARGET->window()) || m_beginDragSizeXY == Vector2D())) {
        m_dragIntentController.cancel();
        g_layoutManager->cancelDragTarget();
        return;
    }

    if (m_spatialGrabOwned) {
        if (!m_dragThresholdReached && m_beginDragXY.distanceSq(mousePos) > std::pow(*PDRAGTHRESHOLD, 2))
            m_dragThresholdReached = true;
        m_dragIntentController.update(mousePos, m_dragThresholdReached);
        Luminophore::spatialGrabController()->update(mousePos.x, mousePos.y);
        return; // Retain this branch even after the spatial controller cancels.
    }

    // Yoink dragged window here instead if using drag_threshold and it has been reached
    if (*PDRAGTHRESHOLD > 0 && !m_dragThresholdReached) {
        if ((m_beginDragXY.distanceSq(mousePos) <= std::pow(*PDRAGTHRESHOLD, 2) && m_beginDragXY == m_lastDragXY))
            return;
        m_dragThresholdReached = true;
        if (updateDragWindow()) {
            m_dragIntentController.cancel();
            return;
        }
    }

    static auto TIMER = std::chrono::high_resolution_clock::now(), MSTIMER = TIMER;

    const auto  DELTA     = Vector2D(mousePos.x - m_beginDragXY.x, mousePos.y - m_beginDragXY.y);
    const auto  TICKDELTA = Vector2D(mousePos.x - m_lastDragXY.x, mousePos.y - m_lastDragXY.y);

    static auto SNAPENABLED = CConfigValue<Config::INTEGER>("general:snap:enabled");

    const auto  TIMERDELTA    = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - TIMER).count();
    const auto  MSDELTA       = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - MSTIMER).count();
    const auto  MSMONITOR     = 1000.0 / (g_pHyprRenderer->m_mostHzMonitor ? g_pHyprRenderer->m_mostHzMonitor->m_refreshRate : 60.0);
    static int  totalMs       = 0;
    bool        canSkipUpdate = true;

    MSTIMER = std::chrono::high_resolution_clock::now();

    if (m_mouseMoveEventCount == 1)
        totalMs = 0;

    if (MSMONITOR > 16.0) {
        totalMs += MSDELTA < MSMONITOR ? MSDELTA : std::round(totalMs * 1.0 / m_mouseMoveEventCount);
        m_mouseMoveEventCount += 1;

        // check if time-window is enough to skip update on 60hz monitor
        canSkipUpdate = std::clamp(MSMONITOR - TIMERDELTA, 0.0, MSMONITOR) > totalMs * 1.0 / m_mouseMoveEventCount;
    }

    if ((abs(TICKDELTA.x) < 1.f && abs(TICKDELTA.y) < 1.f) || (TIMERDELTA < MSMONITOR && canSkipUpdate && (m_dragMode != MBIND_MOVE)))
        return;

    TIMER = std::chrono::high_resolution_clock::now();

    m_lastDragXY = mousePos;

    DRAGGINGTARGET->damageEntire();

    const auto MOTIONWINDOW = DRAGGINGTARGET->window();
    const bool TRACKMOTION  = validMapped(MOTIONWINDOW);
    CBox       previousFull;

    if (TRACKMOTION)
        previousFull = MOTIONWINDOW->getFullWindowBoundingBox();

    if (m_dragMode == MBIND_MOVE) {

        Vector2D newPos  = m_beginDragPositionXY + DELTA;
        Vector2D newSize = DRAGGINGTARGET->position().size();

        if (*SNAPENABLED && !m_draggingTiled)
            g_layoutManager->performSnap(newPos, newSize, DRAGGINGTARGET, MBIND_MOVE, -1, m_beginDragSizeXY);

        newPos = newPos.round();

        DRAGGINGTARGET->setPositionGlobal({newPos, newSize});
        DRAGGINGTARGET->warpPositionSize();
    } else if (isResizeMode(m_dragMode)) {
        if (DRAGGINGTARGET->floating()) {

            Vector2D MINSIZE = DRAGGINGTARGET->minSize().value_or(Vector2D{MIN_WINDOW_SIZE, MIN_WINDOW_SIZE});
            Vector2D MAXSIZE = DRAGGINGTARGET->maxSize().value_or(Math::VECTOR2D_MAX);

            Vector2D newSize = m_beginDragSizeXY;
            Vector2D newPos  = m_beginDragPositionXY;

            if (edgeRight(m_grabbedCorner))
                newSize.x += DELTA.x;
            else if (edgeLeft(m_grabbedCorner))
                newSize.x -= DELTA.x;

            if (edgeBottom(m_grabbedCorner))
                newSize.y += DELTA.y;
            else if (edgeTop(m_grabbedCorner))
                newSize.y -= DELTA.y;

            eMouseBindMode mode = m_dragMode;
            if (DRAGGINGTARGET->window() && DRAGGINGTARGET->window()->m_ruleApplicator->keepAspectRatio().valueOrDefault() && mode != MBIND_RESIZE_BLOCK_RATIO)
                mode = MBIND_RESIZE_FORCE_RATIO;

            if (m_beginDragSizeXY.x >= 1 && m_beginDragSizeXY.y >= 1 && mode == MBIND_RESIZE_FORCE_RATIO) {

                const float RATIO = m_beginDragSizeXY.y / m_beginDragSizeXY.x;

                if (MINSIZE.x * RATIO > MINSIZE.y)
                    MINSIZE = Vector2D(MINSIZE.x, MINSIZE.x * RATIO);
                else
                    MINSIZE = Vector2D(MINSIZE.y / RATIO, MINSIZE.y);

                if (MAXSIZE.x * RATIO < MAXSIZE.y)
                    MAXSIZE = Vector2D(MAXSIZE.x, MAXSIZE.x * RATIO);
                else
                    MAXSIZE = Vector2D(MAXSIZE.y / RATIO, MAXSIZE.y);

                if (newSize.x * RATIO > newSize.y)
                    newSize = Vector2D(newSize.x, newSize.x * RATIO);
                else
                    newSize = Vector2D(newSize.y / RATIO, newSize.y);
            }

            newSize = newSize.clamp(MINSIZE, MAXSIZE);

            if (edgeLeft(m_grabbedCorner))
                newPos.x += (m_beginDragSizeXY - newSize).x;
            if (edgeTop(m_grabbedCorner))
                newPos.y += (m_beginDragSizeXY - newSize).y;

            if (*SNAPENABLED) {
                g_layoutManager->performSnap(newPos, newSize, DRAGGINGTARGET, mode, m_grabbedCorner, m_beginDragSizeXY);
                newSize = newSize.clamp(MINSIZE, MAXSIZE);
            }

            CBox wb = {newPos, newSize};
            wb.round();

            DRAGGINGTARGET->setPositionGlobal(wb);
            DRAGGINGTARGET->warpPositionSize();
        } else {
            g_layoutManager->resizeTarget(TICKDELTA, DRAGGINGTARGET, m_grabbedCorner);
            DRAGGINGTARGET->warpPositionSize();
        }
    }

    if (TRACKMOTION)
        MOTIONWINDOW->recordMotionBlur(previousFull, MOTIONWINDOW->getFullWindowBoundingBox());

    // get middle point
    Vector2D middle = DRAGGINGTARGET->position().middle();

    // and check its monitor
    const auto PMONITOR = State::monitorState()->query().vec(middle).run();

    m_dragIntentController.update(mousePos, m_dragMode == MBIND_MOVE && m_dragThresholdReached);

    if (PMONITOR && PMONITOR->m_activeWorkspace && DRAGGINGTARGET->floating() /* If we're resizing a tiled target, don't do this */) {
        const auto WS = PMONITOR->m_activeSpecialWorkspace ? PMONITOR->m_activeSpecialWorkspace : PMONITOR->m_activeWorkspace;
        DRAGGINGTARGET->assignToSpace(WS->m_space);
    }

    DRAGGINGTARGET->damageEntire();
    if (m_spatialGrabOwned)
        Luminophore::spatialGrabController()->update(mousePos.x, mousePos.y);
}
