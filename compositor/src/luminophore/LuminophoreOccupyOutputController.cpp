#include "LuminophoreOccupyOutputController.hpp"

#include "../managers/fullscreen/FullscreenController.hpp"

#include <format>
#include "../desktop/view/Window.hpp"
#include "../output/Monitor.hpp"
#include "../managers/EventManager.hpp"
#include "../helpers/MiscFunctions.hpp"

using namespace Luminophore;

std::string CLuminophoreOccupyOutputController::requestKey(const PHLWINDOW& window, const std::string& serial) const {
    return std::format("{:x}:{}", rc<uintptr_t>(window.get()), serial);
}

bool CLuminophoreOccupyOutputController::rememberRequest(const std::string& key) {
    if (m_seenRequests.contains(key))
        return false;

    m_seenRequests.emplace(key);
    m_seenRequestOrder.emplace_back(key);
    if (m_seenRequestOrder.size() > MAX_SEEN_REQUESTS) {
        m_seenRequests.erase(m_seenRequestOrder.front());
        m_seenRequestOrder.pop_front();
    }

    return true;
}

bool CLuminophoreOccupyOutputController::apply(const PHLWINDOW& window, eOccupyOutputAction action, const std::string& serial) {
    if (!window)
        return false;

    if (!serial.empty() && !rememberRequest(requestKey(window, serial)))
        return false;

    const bool OCCUPIED = Fullscreen::controller()->isFullscreen(window);
    const bool OCCUPY   = action == OCCUPY_OUTPUT_SET || (action == OCCUPY_OUTPUT_TOGGLE && !OCCUPIED);

    if ((OCCUPY && OCCUPIED) || (!OCCUPY && !OCCUPIED))
        return true;

    Fullscreen::controller()->setFullscreenMode(window, OCCUPY ? Fullscreen::FSMODE_FULLSCREEN : Fullscreen::FSMODE_NONE, std::nullopt, true);
    const bool enabled = Fullscreen::controller()->isFullscreen(window);
    const auto monitor = window->m_monitor.lock();
    if (g_pEventManager && monitor)
        g_pEventManager->postEvent(SHyprIPCEvent{.event = "luminophorefullscreen",
                                                 .data  = std::format(R"({{"schema":1,"connector":"{}","window":"0x{:x}","enabled":{},"applied":{}}})",
                                                                      escapeJSONStrings(monitor->m_name), rc<uintptr_t>(window.get()), enabled, enabled == OCCUPY)});
    return enabled == OCCUPY;
}

UP<CLuminophoreOccupyOutputController>& Luminophore::occupyOutputController() {
    static UP<CLuminophoreOccupyOutputController> controller = makeUnique<CLuminophoreOccupyOutputController>();
    return controller;
}
