#include "LuminophoreSpatialMotion.hpp"
#include <limits>
std::optional<SLuminophoreSpatialCommitEntry> CLuminophoreSpatialMotion::outside(const SLuminophoreSpatialCommitEntry& entry, const SLuminophoreBoardPoint& point, const SLuminophoreViewRect& view,
                                                                   const SLuminophorePhysicalBox& output) {
    if (!entry.visible || view.contains(point))
        return std::nullopt;
    int64_t dx = 0, dy = 0;
    if (point.x < view.origin.x)
        dx = -int64_t(output.width);
    else if (static_cast<__int128_t>(point.x) >= static_cast<__int128_t>(view.origin.x) + view.columns)
        dx = output.width;
    else if (point.y < view.origin.y)
        dy = -int64_t(output.height);
    else
        dy = output.height;
    auto       result = entry;
    const auto shift  = [&](SLuminophorePhysicalBox& box) {
        const int64_t x = int64_t(box.x) + dx, y = int64_t(box.y) + dy;
        if (x < INT32_MIN || x > INT32_MAX || y < INT32_MIN || y > INT32_MAX)
            return false;
        box.x = static_cast<int>(x);
        box.y = static_cast<int>(y);
        return true;
    };
    if (!shift(result.clientBox))
        return std::nullopt;
    for (auto& f : result.fragments)
        if (!shift(f.box))
            return std::nullopt;
    return result;
}
