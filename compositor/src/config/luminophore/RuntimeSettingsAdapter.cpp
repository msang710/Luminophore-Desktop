#include "RuntimeSettingsAdapter.hpp"
#include <algorithm>
#include <stdexcept>
#include <utility>

using namespace Luminophore::Settings;

const std::vector<std::string>& CRuntimeSettingsAdapter::supported() {
    static const std::vector<std::string> keys = [] {
        std::vector<std::string> result;
        for (const auto& [key, value] : defaults())
            result.push_back(key);
        return result;
    }();
    return keys;
}

static bool matchesNative(const std::string& key, const Value& actual, const Value& expected) {
    if (actual.index() != expected.index())
        return false;
    if (const auto* value = std::get_if<double>(&expected); value &&
        (key == "compositor.zoom_factor" || key == "compositor.shadow_scale" || key == "compositor.active_opacity" || key == "compositor.inactive_opacity" ||
         key == "compositor.dim_special" || key == "compositor.fullscreen_opacity" || key == "compositor.dim_strength" || key == "compositor.dim_around" ||
         key == "input.sensitivity" || key == "input.cursor_inactive_timeout" || key == "input.follow_mouse_threshold" || key == "input.scroll_factor" ||
         key == "touchpad.scroll_factor" || key.starts_with("tablettool.pressure_range_") || key.starts_with("compositor.shadow_offset_") || key.starts_with("tablet.region_") ||
         key.starts_with("tablet.active_area_")))
        return std::get<double>(actual) == static_cast<double>(static_cast<float>(*value));
    return actual == expected;
}

CRuntimeSettingsAdapter::CRuntimeSettingsAdapter(Snapshot initial, std::map<std::string, SRuntimeSlot> slots, std::function<void()> refresh, std::function<void()> restoreRefresh) :
    m_canonical(std::move(initial)), m_slots(std::move(slots)), m_refresh(std::move(refresh)), m_restoreRefresh(restoreRefresh ? std::move(restoreRefresh) : m_refresh) {
    if (m_canonical.size() != defaults().size() || !validate(m_canonical).empty() || m_slots.size() != supported().size() || !m_refresh)
        throw std::invalid_argument("invalid runtime adapter");
    for (const auto& key : supported()) {
        if (!m_slots.contains(key) || !m_slots.at(key).read || !m_slots.at(key).write || !matchesNative(key, m_slots.at(key).read(), m_canonical.at(key)))
            throw std::invalid_argument("runtime baseline mismatch: " + key);
    }
}

Snapshot CRuntimeSettingsAdapter::read() const {
    Snapshot result = m_canonical;
    for (const auto& [key, slot] : m_slots) {
        const auto actual = slot.read();
        // Exact native float representation, not a broad epsilon. Preserve
        // canonical double only when the actual consumer agrees bit-for-value.
        if (!matchesNative(key, actual, m_canonical.at(key)))
            result[key] = actual;
    }
    return result;
}

void CRuntimeSettingsAdapter::apply(const Snapshot& candidate) {
    applyValues(candidate, false);
}
void CRuntimeSettingsAdapter::restore(const Snapshot& candidate) {
    applyValues(candidate, true);
}
void CRuntimeSettingsAdapter::applyValues(const Snapshot& candidate, bool restoring) {
    if (candidate.size() != defaults().size() || !validate(candidate).empty())
        throw std::invalid_argument("invalid runtime candidate");
    for (const auto& [key, value] : candidate) {
        if (!m_slots.contains(key) && value != m_canonical.at(key))
            throw std::invalid_argument("runtime field not migrated: " + key);
    }
    // Resolve/read every destination before any write. Missing/wrong-type
    // consumers fail without partially touching the remaining destinations.
    for (const auto& [key, slot] : m_slots) {
        if (slot.validate)
            slot.validate(candidate.at(key));
        if (slot.writable)
            slot.writable();
        else if (slot.read().index() != candidate.at(key).index())
            throw std::runtime_error("runtime type changed: " + key);
    }
    for (const auto& [key, slot] : m_slots)
        slot.write(candidate.at(key));
    m_canonical = candidate;
    (restoring ? m_restoreRefresh : m_refresh)();
}
