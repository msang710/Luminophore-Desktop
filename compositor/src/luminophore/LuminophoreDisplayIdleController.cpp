#include "LuminophoreDisplayIdleController.hpp"

#include "../Compositor.hpp"
#include "../config/ConfigValue.hpp"
#include "../config/shared/actions/ConfigActions.hpp"
#include "../managers/eventLoop/EventLoopManager.hpp"
#include "../managers/screenshare/ScreenshareManager.hpp"
#include "../protocols/IdleNotify.hpp"

#include <chrono>

using namespace Luminophore;

CLuminophoreDisplayIdleController::CLuminophoreDisplayIdleController() = default;

CLuminophoreDisplayIdleController::~CLuminophoreDisplayIdleController() {
    if (!m_timer || !g_pEventLoopManager)
        return;
    m_timer->cancel();
    g_pEventLoopManager->removeTimer(m_timer);
}

void CLuminophoreDisplayIdleController::settingsChanged() {
    // Reset only when the timeout preference changes, never on unrelated applies.
    arm();
}

void CLuminophoreDisplayIdleController::physicalActivity() {
    static auto PIDLEMINUTES = CConfigValue<Config::INTEGER>("misc:luminophore_monitor_idle_minutes");
    if (*PIDLEMINUTES <= 0 || !g_pEventLoopManager)
        return;

    if (!g_pCompositor->m_dpmsStateOn)
        Config::Actions::dpms(Config::Actions::TOGGLE_ACTION_ENABLE, std::nullopt);
    arm();
}

void CLuminophoreDisplayIdleController::inhibitorChanged() {
    if (m_timer)
        arm();
}

bool CLuminophoreDisplayIdleController::inhibited() const {
    return (PROTO::idle && PROTO::idle->inhibited()) || (Screenshare::mgr() && Screenshare::mgr()->hasActiveSessions());
}

void CLuminophoreDisplayIdleController::arm() {
    static auto PIDLEMINUTES = CConfigValue<Config::INTEGER>("misc:luminophore_monitor_idle_minutes");
    if (*PIDLEMINUTES <= 0 || !g_pEventLoopManager) {
        if (m_timer) {
            m_timer->cancel();
            if (g_pEventLoopManager)
                g_pEventLoopManager->removeTimer(m_timer);
            m_timer.reset();
        }
        return;
    }

    const auto TIMEOUT = std::chrono::minutes(*PIDLEMINUTES);
    if (!m_timer) {
        m_timer = makeShared<CEventLoopTimer>(TIMEOUT, [this](SP<CEventLoopTimer>, void*) { expire(); }, nullptr);
        g_pEventLoopManager->addTimer(m_timer);
    } else
        m_timer->updateTimeout(TIMEOUT);
}

void CLuminophoreDisplayIdleController::expire() {
    static auto PIDLEMINUTES = CConfigValue<Config::INTEGER>("misc:luminophore_monitor_idle_minutes");
    if (*PIDLEMINUTES <= 0)
        return;

    if (inhibited()) {
        // Re-evaluate periodically without treating application activity as
        // local physical input. No process or session is ever suspended.
        m_timer->updateTimeout(std::chrono::seconds(30));
        return;
    }

    Config::Actions::dpms(Config::Actions::TOGGLE_ACTION_DISABLE, std::nullopt);
}

UP<CLuminophoreDisplayIdleController>& Luminophore::displayIdleController() {
    static UP<CLuminophoreDisplayIdleController> controller = makeUnique<CLuminophoreDisplayIdleController>();
    return controller;
}
