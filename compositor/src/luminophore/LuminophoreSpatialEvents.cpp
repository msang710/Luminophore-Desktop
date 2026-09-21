#include "LuminophoreSpatialEvents.hpp"
#include "../managers/EventManager.hpp"
#include "../helpers/MiscFunctions.hpp"
#include <algorithm>
#include <format>
using namespace Luminophore;
std::string Luminophore::SpatialEvents::serializeAction(eSpatialAction action, eLuminophoreSpatialDirection direction, const SLuminophoreSpatialSnapshot& before,
                                                 const SLuminophoreSpatialTransactionResult& result, std::optional<LuminophoreWindowKey> eventKey, std::optional<LuminophoreWindowKey> key,
                                                 uint64_t eventOutput, const std::string& connector, bool visible, const std::string& reason) {
    const bool applied       = result.status == eLuminophoreSpatialTransactionStatus::APPLIED;
    const auto directionName = direction == eLuminophoreSpatialDirection::LEFT ? "left" :
        direction == eLuminophoreSpatialDirection::RIGHT                       ? "right" :
        direction == eLuminophoreSpatialDirection::UP                          ? "up" :
                                                                          "down";
    const auto pointJSON     = [](const SLuminophoreSpatialSnapshot& state, std::optional<LuminophoreWindowKey> selected) {
        const auto found = std::ranges::find_if(state.tiled, [&](const auto& item) { return selected && item.key == *selected; });
        return found == state.tiled.end() ? std::string{"null"} : std::format("[{},{}]", found->point.x, found->point.y);
    };
    const auto rectJSON = [&](const SLuminophoreSpatialSnapshot& state) {
        const auto found = std::ranges::find(state.outputViews, eventOutput, &SLuminophoreOutputView::outputID);
        return found == state.outputViews.end() ? std::string{"null"} :
                                                  std::format("[{},{},{},{}]", found->rect.origin.x, found->rect.origin.y, found->rect.columns, found->rect.rows);
    };
    const auto actionName = action == eSpatialAction::FOCUS_DIRECTION ? "focus" :
        action == eSpatialAction::MOVE_WINDOW                         ? "window-move" :
        action == eSpatialAction::MOVE_VIEW                           ? "view-move" :
        action == eSpatialAction::ADJUST_VIEW                         ? "view-resize" :
        action == eSpatialAction::TOGGLE_DESKTOP                      ? "desktop" :
                                                                        "wide";
    return std::format(
        R"({{"schema":1,"action":"{}","applied":{},"direction":"{}","visible":{},"reason":"{}","revision":{},"topologyRevision":{},"output":{},"connector":"{}","window":"0x{:x}","fromPoint":{},"toPoint":{},"fromRect":{},"toRect":{},"displayOrigin":[{},{}]}})",
        actionName, applied, directionName, visible, reason, result.snapshot.revision, result.snapshot.outputTopologyRevision, eventOutput, escapeJSONStrings(connector),
        key.value_or(0), pointJSON(before, eventKey), pointJSON(result.snapshot, key), rectJSON(before), rectJSON(result.snapshot), result.snapshot.extent.columns / 2,
        result.snapshot.extent.rows / 2);
}
void Luminophore::SpatialEvents::action(eSpatialAction action, eLuminophoreSpatialDirection direction, const SLuminophoreSpatialSnapshot& before, const SLuminophoreSpatialTransactionResult& result,
                                 std::optional<LuminophoreWindowKey> eventKey, std::optional<LuminophoreWindowKey> key, uint64_t eventOutput, const std::string& connector, bool visible,
                                 const std::string& reason) {
    if (g_pEventManager)
        g_pEventManager->postEvent(
            SHyprIPCEvent{.event = "luminophorespatialaction", .data = serializeAction(action, direction, before, result, eventKey, key, eventOutput, connector, visible, reason)});
}

bool Luminophore::SpatialEvents::stateChanged(uint64_t revision) {
    if (!g_pEventManager)
        return false;
    g_pEventManager->postEvent(SHyprIPCEvent{.event = "luminophorespatial", .data = std::format("{}", revision)});
    return true;
}
void Luminophore::SpatialEvents::floatingBlocked(LuminophoreWindowKey key) {
    if (g_pEventManager)
        g_pEventManager->postEvent(SHyprIPCEvent{.event = "luminophorespatialblocked", .data = std::format("floating-sync,0x{:x}", key)});
}

void Luminophore::SpatialEvents::historyCancelFailed(bool degraded) {
    if (g_pEventManager)
        g_pEventManager->postEvent(SHyprIPCEvent{"luminophorespatialhistory", std::format(R"({{"action":"cancel","status":"{}"}})", degraded ? "recovery-failed" : "cancel-failed")});
}
