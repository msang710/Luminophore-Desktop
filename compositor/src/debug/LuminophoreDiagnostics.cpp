#include "LuminophoreDiagnostics.hpp"
#include "../helpers/math/Math.hpp"

#include <algorithm>
#include <cmath>
#include <ranges>

using namespace Render;

static int64_t monotonicMicroseconds() {
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

void CLuminophoreDiagnostics::recordEnqueued() {
    m_counters.enqueued++;
}

void CLuminophoreDiagnostics::recordDrawn() {
    m_counters.drawn++;
}

uint64_t CLuminophoreDiagnostics::recordFrameRequest(MONITORID monitor, eLuminophoreFrameReason reason, const CBox& damage, bool coalesced) {
    m_counters.frameRequests++;

    const auto SEQUENCE = m_nextSequence++;
    m_frameEvents.emplace_back(SLuminophoreFrameEvent{
        .monitor       = monitor,
        .sequence      = SEQUENCE,
        .reason        = reason,
        .requestedAtUs = monotonicMicroseconds(),
        .damageArea    = static_cast<uint64_t>(std::max(0.0, std::round(damage.w)) * std::max(0.0, std::round(damage.h))),
        .coalesced     = coalesced,
    });
    while (m_frameEvents.size() > MAX_FRAME_EVENTS)
        m_frameEvents.pop_front();
    return SEQUENCE;
}

void CLuminophoreDiagnostics::recordPresented(MONITORID monitor) {
    const auto NOW = monotonicMicroseconds();
    const auto IT  = std::ranges::find_if(m_frameEvents | std::views::reverse, [monitor](const auto& event) { return event.monitor == monitor && event.presentedAtUs == 0; });
    if (IT != m_frameEvents.rend())
        IT->presentedAtUs = NOW;
}

void CLuminophoreDiagnostics::recordShaderFailure() {
    m_counters.shaderFailures++;
}

void CLuminophoreDiagnostics::recordSkipped() {
    m_counters.skipped++;
}

SLuminophoreDiagnosticsSnapshot CLuminophoreDiagnostics::snapshot() const {
    return m_counters;
}

std::vector<SLuminophoreFrameEvent> CLuminophoreDiagnostics::frameEvents() const {
    return {m_frameEvents.begin(), m_frameEvents.end()};
}
