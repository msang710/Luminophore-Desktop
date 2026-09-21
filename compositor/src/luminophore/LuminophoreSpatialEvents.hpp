#pragma once
#include "LuminophoreSpatialTransaction.hpp"
namespace Luminophore {
    enum class eSpatialAction : uint8_t {
        MOVE_VIEW,
        ADJUST_VIEW,
        MOVE_WINDOW,
        FOCUS_DIRECTION,
        TOGGLE_WIDE,
        TOGGLE_DESKTOP,
    };

}
namespace Luminophore::SpatialEvents {
    std::string serializeAction(eSpatialAction action, eLuminophoreSpatialDirection direction, const SLuminophoreSpatialSnapshot& before, const SLuminophoreSpatialTransactionResult& result,
                                std::optional<LuminophoreWindowKey> eventKey, std::optional<LuminophoreWindowKey> key, uint64_t eventOutput, const std::string& connector, bool visible,
                                const std::string& reason);
    void        action(eSpatialAction action, eLuminophoreSpatialDirection direction, const SLuminophoreSpatialSnapshot& before, const SLuminophoreSpatialTransactionResult& result,
                       std::optional<LuminophoreWindowKey> eventKey, std::optional<LuminophoreWindowKey> key, uint64_t eventOutput, const std::string& connector, bool visible,
                       const std::string& reason);
    bool        stateChanged(uint64_t revision);
    void        historyCancelFailed(bool degraded);
    void        floatingBlocked(LuminophoreWindowKey key);
}
