#include "LuminophoreSpatialResizeGesture.hpp"
#include <algorithm>
#include <cmath>
#include <climits>
#include <limits>
using namespace Luminophore::Spatial;
void CResizeGesture::begin(bool active) {
    m_active     = active;
    m_terminated = false;
    m_horizontal.reset();
    m_vertical.reset();
}
bool CResizeGesture::active() const {
    return m_active;
}
bool CResizeGesture::terminated() const {
    return m_terminated;
}
std::optional<SResizeRequest> CResizeGesture::select(const SMesh& mesh, const SFill& fill, WindowKey window, double pointerX, double pointerY, eSide side, double d) {
    if (m_terminated || !std::isfinite(d) || !std::isfinite(pointerX) || !std::isfinite(pointerY))
        return std::nullopt;
    if (std::abs(d) < 0.5 || std::abs(d) > INT_MAX)
        return std::nullopt;
    auto& held = (side == eSide::LEFT || side == eSide::RIGHT) ? m_horizontal : m_vertical;
    if (m_active && held) {
        SResizeRequest r{mesh.revision, *held, side, static_cast<int64_t>(std::llround(d)), 100};
        const auto     probe = solveWindowResize(mesh, fill, window, r);
        if (probe.status == eResizeStatus::INVALID) {
            m_terminated = true;
            return std::nullopt;
        }
        return r;
    }
    const auto regions = regionsForOwnership(mesh, fill);
    if (!regions)
        return std::nullopt;
    const auto region = std::ranges::find(*regions, window, &SRegion::owner);
    if (region == regions->end())
        return std::nullopt;
    const bool                    horizontal = side == eSide::LEFT || side == eSide::RIGHT;
    const auto&                   bounds     = region->boxes.front();
    const auto                    boundary   = horizontal ? bounds.x + (side == eSide::RIGHT ? bounds.width : 0) : bounds.y + (side == eSide::BOTTOM ? bounds.height : 0);
    std::optional<SResizeRequest> best;
    double                        distance = std::numeric_limits<double>::infinity();
    for (const auto& face : mesh.faces) {
        SResizeRequest request{mesh.revision, face.id, side, static_cast<int64_t>(std::llround(d)), 100};
        const auto     claim = std::ranges::find_if(fill.rectangles, [&](const auto& rect) { return rect.logical.contains(face.provenance.origin); });
        if (claim == fill.rectangles.end() || claim->owner != window)
            continue;
        const auto&  b          = face.box;
        const bool   horizontal = side == eSide::LEFT || side == eSide::RIGHT;
        const double edge       = horizontal ? b.x + (side == eSide::RIGHT ? b.width : 0) : b.y + (side == eSide::BOTTOM ? b.height : 0);
        if (edge != boundary)
            continue;
        const double tangent = horizontal ? pointerY : pointerX, begin = horizontal ? b.y : b.x, end = begin + (horizontal ? b.height : b.width);
        const double score = std::abs((horizontal ? pointerX : pointerY) - edge) + std::max({0.0, begin - tangent, tangent - end});
        if (score < distance) {
            distance = score;
            best     = request;
        }
    }
    if (m_active && best)
        held = best->face;
    return best;
}
