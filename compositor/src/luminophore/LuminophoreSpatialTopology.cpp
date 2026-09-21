#include "LuminophoreSpatialTopology.hpp"
#include "../state/MonitorState.hpp"
#include "../output/Monitor.hpp"
#include "../pointer/PointerManager.hpp"
#include <algorithm>
#include <cmath>
using namespace Luminophore;
std::vector<SLuminophorePhysicalOutput> Luminophore::SpatialTopology::physicalOutputs() {
    std::vector<SLuminophorePhysicalOutput> result;
    for (const auto& monitor : State::monitorState()->monitors()) {
        if (!monitor)
            continue;
        const auto box  = monitor->logicalBoxMinusReserved();
        const auto full = monitor->logicalBox();
        result.emplace_back(SLuminophorePhysicalOutput{
            .id         = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get())),
            .name       = monitor->m_name,
            .box        = {.x = static_cast<int>(box.x), .y = static_cast<int>(box.y), .width = static_cast<int>(box.w), .height = static_cast<int>(box.h)},
            .scaleMilli = static_cast<int>(std::lround(monitor->m_scale * 1000.0)),
            .transform  = static_cast<int>(monitor->m_transform),
            .logicalBox = {static_cast<int>(full.x), static_cast<int>(full.y), static_cast<int>(full.w), static_cast<int>(full.h)},
        });
    }
    return result;
}

PHLMONITOR Luminophore::SpatialTopology::monitorForOutput(uint64_t outputID) {
    for (const auto& monitor : State::monitorState()->monitors()) {
        if (monitor && static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get())) == outputID)
            return monitor;
    }
    return nullptr;
}

std::optional<uint64_t> Luminophore::SpatialTopology::selectedOutputID(const std::vector<SLuminophoreOutputView>& views) {
    const auto position = Pointer::mgr()->position();
    for (const auto& monitor : State::monitorState()->monitors()) {
        if (!monitor || !monitor->logicalBox().containsPoint(position))
            continue;
        const auto outputID = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(monitor.get()));
        if (std::ranges::any_of(views, [outputID](const auto& view) { return view.outputID == outputID; }))
            return outputID;
    }
    return std::nullopt;
}
