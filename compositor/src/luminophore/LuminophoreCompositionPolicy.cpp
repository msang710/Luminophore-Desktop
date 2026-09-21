#include "LuminophoreCompositionPolicy.hpp"

#include "LuminophoreShellProjection.hpp"
#include "LuminophoreMonitorTransaction.hpp"
#include "LuminophoreSpatialRuntime.hpp"
#include "LuminophoreSpatialGrabController.hpp"
#include "../desktop/view/Window.hpp"
#include "../output/Monitor.hpp"

using namespace Luminophore;

bool CLuminophoreCompositionPolicy::blocksDirectScanout(PHLMONITOR monitor) const {
    return spatialGrabController()->active() || spatialRuntime()->desktopExposed() || shellProjection()->blocksDirectScanout(monitor);
}

eShellProjectionPlane CLuminophoreCompositionPolicy::effectiveShellPlane(PHLMONITOR monitor, eShellProjectionPlane requested) const {
    // The committed Shell plane does not depend on client visibility or mode.
    return monitor ? requested : SHELL_PROJECTION_BOTTOM;
}

bool CLuminophoreCompositionPolicy::showsWindowEffect(PHLWINDOW window) const {
    return window && !window->m_X11DoesntWantBorders && !window->isX11OverrideRedirect() && !window->parent() && !window->isModal() &&
        window->m_ruleApplicator->decorate().valueOr(true) && !spatialRuntime()->isWideWindow(window) &&
        monitorTransaction()->windowState(window).mode != WINDOW_PRESENTATION_OCCUPY_OUTPUT;
}

UP<CLuminophoreCompositionPolicy>& Luminophore::compositionPolicy() {
    static UP<CLuminophoreCompositionPolicy> policy = makeUnique<CLuminophoreCompositionPolicy>();
    return policy;
}
