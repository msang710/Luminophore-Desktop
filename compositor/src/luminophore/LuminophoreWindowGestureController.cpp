#include "LuminophoreWindowGestureController.hpp"

#include "../desktop/state/WindowPlacementController.hpp"
#include "../desktop/state/WindowState.hpp"
#include "../desktop/view/Window.hpp"
#include "../layout/space/Space.hpp"
#include "../managers/fullscreen/FullscreenController.hpp"
#include "../output/Monitor.hpp"

using namespace Luminophore;

UP<CLuminophoreWindowGestureController>& Luminophore::windowGestureController() {
    static UP<CLuminophoreWindowGestureController> controller = makeUnique<CLuminophoreWindowGestureController>();
    return controller;
}

bool CLuminophoreWindowGestureController::minimizeOthers(const PHLWINDOW& anchor) {
    if (!anchor || !anchor->m_isMapped || !anchor->m_monitor)
        return false;

    bool changed = false;
    for (const auto& window : Desktop::windowState()->windows()) {
        if (!window || window == anchor || !window->m_isMapped || window->isMinimized() || window->m_monitor != anchor->m_monitor || window->m_isFloating || window->m_pinned ||
            !window->m_workspace || window->m_workspace->m_isSpecialWorkspace || Fullscreen::controller()->isFullscreen(window))
            continue;
        changed |= Desktop::windowPlacementController()->minimize(window);
    }
    return changed;
}

bool CLuminophoreWindowGestureController::minimize(const PHLWINDOW& window) {
    if (!window || !window->m_isMapped || window->m_pinned || !window->m_workspace || window->m_workspace->m_isSpecialWorkspace || Fullscreen::controller()->isFullscreen(window))
        return false;

    return Desktop::windowPlacementController()->minimize(window);
}

bool CLuminophoreWindowGestureController::restore(const PHLWINDOW& window) {
    return Desktop::windowPlacementController()->restore(window);
}
