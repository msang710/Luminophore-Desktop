#include "MonitorPositionController.hpp"

#include "../config/shared/monitor/MonitorRule.hpp"
#include "../debug/log/Logger.hpp"
#include "../macros.hpp"

#include <algorithm>

using namespace State;

UP<CMonitorPositionController>& State::monitorPositionController() {
    static UP<CMonitorPositionController> p = makeUnique<CMonitorPositionController>();
    return p;
}

void CMonitorPositionController::arrange(std::span<const SP<Monitor::IMonitorArrangeable>> monitors, bool xwaylandForceZeroScaling) const {
    std::vector<SP<Monitor::IMonitorArrangeable>> toArrange(monitors.begin(), monitors.end());
    std::vector<SP<Monitor::IMonitorArrangeable>> arranged;
    arranged.reserve(toArrange.size());

    for (auto it = toArrange.begin(); it != toArrange.end();) {
        auto m = *it;

        if (const auto POS = m->explicitPosition(); POS.has_value()) {
            Log::logger->log(Log::DEBUG, "arrangeMonitors: {} explicit {:j}", m->name(), *POS);

            m->moveTo(*POS);
            arranged.push_back(m);
            it = toArrange.erase(it);

            if (it == toArrange.end())
                break;

            continue;
        }

        ++it;
    }

    // Variables to store the max and min values of monitors on each axis.
    int  maxXOffsetRight = 0;
    int  maxXOffsetLeft  = 0;
    int  maxYOffsetUp    = 0;
    int  maxYOffsetDown  = 0;

    auto recalcMaxOffsets = [&]() {
        maxXOffsetRight = 0;
        maxXOffsetLeft  = 0;
        maxYOffsetUp    = 0;
        maxYOffsetDown  = 0;

        for (auto const& m : arranged) {
            maxXOffsetRight = std::max<double>(m->position().x + m->size().x, maxXOffsetRight);
            maxXOffsetLeft  = std::min<double>(m->position().x, maxXOffsetLeft);
            maxYOffsetDown  = std::max<double>(m->position().y + m->size().y, maxYOffsetDown);
            maxYOffsetUp    = std::min<double>(m->position().y, maxYOffsetUp);
        }
    };

    // Iterates through all non-explicitly placed monitors.
    for (auto const& m : toArrange) {
        recalcMaxOffsets();

        Vector2D newPosition = {0, 0};
        switch (m->autoDirection()) {
            case Config::eAutoDirs::DIR_AUTO_UP: newPosition.y = maxYOffsetUp - m->size().y; break;
            case Config::eAutoDirs::DIR_AUTO_DOWN: newPosition.y = maxYOffsetDown; break;
            case Config::eAutoDirs::DIR_AUTO_LEFT: newPosition.x = maxXOffsetLeft - m->size().x; break;
            case Config::eAutoDirs::DIR_AUTO_RIGHT:
            case Config::eAutoDirs::DIR_AUTO_NONE: newPosition.x = maxXOffsetRight; break;
            case Config::eAutoDirs::DIR_AUTO_CENTER_UP: {
                int width     = maxXOffsetRight - maxXOffsetLeft;
                newPosition.y = maxYOffsetUp - m->size().y;
                newPosition.x = maxXOffsetLeft + (width - m->size().x) / 2;
                break;
            }
            case Config::eAutoDirs::DIR_AUTO_CENTER_DOWN: {
                int width     = maxXOffsetRight - maxXOffsetLeft;
                newPosition.y = maxYOffsetDown;
                newPosition.x = maxXOffsetLeft + (width - m->size().x) / 2;
                break;
            }
            case Config::eAutoDirs::DIR_AUTO_CENTER_LEFT: {
                int height    = maxYOffsetDown - maxYOffsetUp;
                newPosition.x = maxXOffsetLeft - m->size().x;
                newPosition.y = maxYOffsetUp + (height - m->size().y) / 2;
                break;
            }
            case Config::eAutoDirs::DIR_AUTO_CENTER_RIGHT: {
                int height    = maxYOffsetDown - maxYOffsetUp;
                newPosition.x = maxXOffsetRight;
                newPosition.y = maxYOffsetUp + (height - m->size().y) / 2;
                break;
            }
            default: UNREACHABLE();
        }

        m->moveTo(newPosition);
        Log::logger->log(Log::DEBUG, "arrangeMonitors: {} auto {:j}", m->name(), m->position());
        arranged.emplace_back(m);
    }

    // A common scale permits one affine coordinate space across all outputs.
    // Preserve the configured layout: repacking by discovery order makes a
    // spanning window extend outside Xwayland's screen on reversed layouts.
    if (!monitors.empty()) {
        const float scale       = xwaylandForceZeroScaling ? monitors.front()->scale() : 1.F;
        const bool  commonScale = !xwaylandForceZeroScaling || std::ranges::all_of(monitors, [scale](const auto& m) { return m->scale() == scale; });
        if (commonScale) {
            Vector2D origin = monitors.front()->position();
            for (const auto& m : monitors) {
                origin.x = std::min(origin.x, m->position().x);
                origin.y = std::min(origin.y, m->position().y);
            }
            for (const auto& m : monitors) {
                m->setXWaylandPosition(((m->position() - origin) * scale).round());
                m->setXWaylandScale(scale);
            }
            return;
        }
    }

    // Mixed physical scales cannot share this affine transform. Keep the
    // existing packing policy until a mixed-scale spanning policy is defined.
    maxXOffsetRight = 0;
    for (auto const& m : monitors) {
        Log::logger->log(Log::DEBUG, "arrangeMonitors: {} xwayland [{}, {}]", m->name(), maxXOffsetRight, 0);
        m->setXWaylandPosition({maxXOffsetRight, 0});
        maxXOffsetRight += xwaylandForceZeroScaling ? m->transformedSize().x : m->size().x;
        m->setXWaylandScale(xwaylandForceZeroScaling ? m->scale() : 1.F);
    }
}
