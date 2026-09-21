#include "LuminophoreSpatialWindowRegistry.hpp"
#include "../layout/target/Target.hpp"
#include "../desktop/view/Window.hpp"
#include <algorithm>
using namespace Luminophore;
eSpatialParticipation CLuminophoreSpatialWindowRegistry::classifyParticipation(const SSpatialParticipationFacts& facts) {
    if (!facts.mapped || !facts.hasWorkspace)
        return eSpatialParticipation::ABSENT;
    // Parent/modal relationships govern app input, not board occupancy.
    if (facts.pinned || facts.workspaceOverlay || facts.nativeAuxiliary)
        return eSpatialParticipation::EXTERNAL_OVERLAY;
    return eSpatialParticipation::BOARD_ROOT;
}

eSpatialParticipation CLuminophoreSpatialWindowRegistry::participationFor(const SP<Layout::ITarget>& target) {
    const auto window    = target ? target->window() : nullptr;
    const auto workspace = target ? target->workspace() : nullptr;
    const auto role      = workspace ? workspace->role() : WORKSPACE_ROLE_UNKNOWN;
    return classifyParticipation({
        .mapped           = window && window->m_isMapped,
        .hasWorkspace     = !!workspace,
        .hasParent        = window && !!window->parent(),
        .modal            = window && window->isModal(),
        .pinned           = window && window->m_pinned,
        .workspaceOverlay = role == WORKSPACE_ROLE_SERVICE,
        // Native checkBorders() classifies X11 notification/menu/tooltip roles.
        // User decoration rules and ordinary floating windows are not roles.
        .nativeAuxiliary = window && window->m_isX11 && (window->m_X11DoesntWantBorders || window->isX11OverrideRedirect()),
    });
}

SP<Layout::ITarget> CLuminophoreSpatialWindowRegistry::targetFor(LuminophoreWindowKey key) const {
    const auto found = m_targets.find(key);
    return found == m_targets.end() ? nullptr : found->second.lock();
}

std::optional<SLuminophoreSpatialCommitEntry> CLuminophoreSpatialWindowRegistry::presentation(LuminophoreWindowKey key, SLuminophoreBoardPoint host,
                                                                                              const std::vector<SLuminophorePhysicalOutput>& outputs) const {
    const auto found = m_motion.find(key);
    if (found == m_motion.end())
        return std::nullopt;
    SLuminophoreSpatialCommitEntry entry{.key = key, .clientBox = found->second};
    const auto&                    box = found->second;
    for (const auto& output : outputs) {
        const int left   = std::max(box.x, output.box.x);
        const int top    = std::max(box.y, output.box.y);
        const int right  = std::min(box.x + box.width, output.box.x + output.box.width);
        const int bottom = std::min(box.y + box.height, output.box.y + output.box.height);
        if (right <= left || bottom <= top)
            continue;
        entry.fragments.emplace_back(
            SLuminophoreProjectedFragment{.key = key, .point = host, .outputID = output.id, .box = {.x = left, .y = top, .width = right - left, .height = bottom - top}});
        const auto centerX = static_cast<int64_t>(box.x) + box.width / 2;
        const auto centerY = static_cast<int64_t>(box.y) + box.height / 2;
        if (!entry.primaryOutputID ||
            (centerX >= output.box.x && centerX < output.box.x + output.box.width && centerY >= output.box.y && centerY < output.box.y + output.box.height))
            entry.primaryOutputID = output.id;
    }
    entry.visible = !entry.fragments.empty();
    if (!entry.visible)
        entry.clientBox = {};
    return entry;
}

void CLuminophoreSpatialWindowRegistry::remember(LuminophoreWindowKey key, const SP<Layout::ITarget>& target) {
    m_targets[key] = target;
    ++m_generation;
}
void CLuminophoreSpatialWindowRegistry::forget(LuminophoreWindowKey key) {
    m_targets.erase(key);
    ++m_generation;
}
void CLuminophoreSpatialWindowRegistry::clearMotion(LuminophoreWindowKey key) {
    m_motion.erase(key);
    m_activeMotion.erase(key);
    ++m_generation;
}
void CLuminophoreSpatialWindowRegistry::beginMotion(LuminophoreWindowKey key, SLuminophorePhysicalBox box) {
    m_activeMotion.insert(key);
    restoreMotion(key, box);
}
bool CLuminophoreSpatialWindowRegistry::updateMotion(LuminophoreWindowKey key, SLuminophorePhysicalBox box) {
    if (!m_activeMotion.contains(key) || !m_motion.contains(key))
        return false;
    restoreMotion(key, box);
    return true;
}
void CLuminophoreSpatialWindowRegistry::endMotion(LuminophoreWindowKey key) {
    m_activeMotion.erase(key);
    ++m_generation;
}
bool CLuminophoreSpatialWindowRegistry::activeMotion(LuminophoreWindowKey key) const {
    return m_activeMotion.contains(key);
}
bool CLuminophoreSpatialWindowRegistry::hasMotion(LuminophoreWindowKey key) const {
    return m_motion.contains(key);
}
std::optional<SLuminophorePhysicalBox> CLuminophoreSpatialWindowRegistry::takeMotion(LuminophoreWindowKey key) {
    const auto found = m_motion.find(key);
    if (found == m_motion.end())
        return std::nullopt;
    const auto box = found->second;
    m_motion.erase(found);
    ++m_generation;
    return box;
}
void CLuminophoreSpatialWindowRegistry::restoreMotion(LuminophoreWindowKey key, SLuminophorePhysicalBox box) {
    m_motion[key] = box;
    ++m_generation;
}
uint64_t CLuminophoreSpatialWindowRegistry::generation() const {
    return m_generation;
}
