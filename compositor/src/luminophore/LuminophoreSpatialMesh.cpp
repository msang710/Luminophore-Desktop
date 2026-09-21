#include "LuminophoreSpatialMesh.hpp"
#include <algorithm>
#include <limits>
#include <set>

using namespace Luminophore::Spatial;
// Positive integer mul/div without intermediate overflow. Pixel rounding is
// performed once per logical edge, identically for both adjacent faces.
static int64_t scale(int64_t value, int64_t numerator, int64_t denominator) {
    return static_cast<int64_t>((static_cast<__int128_t>(value) * numerator) / denominator);
}
bool SBox::valid() const {
    return width > 0 && height > 0 && checkedAdd(x, width).has_value() && checkedAdd(y, height).has_value();
}
bool Luminophore::Spatial::validateMesh(const SMesh& mesh) {
    if (!mesh.view.valid() || mesh.width <= 0 || mesh.height <= 0 || mesh.faces.empty())
        return false;
    std::set<FaceID> ids;
    struct Event {
        int64_t x;
        bool    entering;
        size_t  index;
    };
    std::vector<Event> events;
    for (size_t i = 0; i < mesh.faces.size(); ++i) {
        const auto& f = mesh.faces[i];
        if (!f.id || !ids.insert(f.id).second || !f.provenance.valid() || !mesh.view.contains(f.provenance.origin) ||
            !mesh.view.contains({*checkedAdd(f.provenance.origin.x, f.provenance.columns) - 1, *checkedAdd(f.provenance.origin.y, f.provenance.rows) - 1}) || !f.box.valid() ||
            f.box.x < 0 || f.box.y < 0 || f.box.x + f.box.width > mesh.width || f.box.y + f.box.height > mesh.height)
            return false;
        events.push_back({f.box.x, true, i});
        events.push_back({f.box.x + f.box.width, false, i});
    }
    std::ranges::sort(events, [](const Event& a, const Event& b) {
        if (a.x != b.x)
            return a.x < b.x;
        return a.entering < b.entering; // Remove before inserting at shared edges.
    });
    std::map<int64_t, size_t> active;
    int64_t                   coverage  = 0;
    int64_t                   previousX = 0;
    for (const auto& event : events) {
        if (event.x > previousX && coverage != mesh.height)
            return false;
        previousX       = event.x;
        const auto& box = mesh.faces[event.index].box;
        if (!event.entering) {
            if (!active.erase(box.y))
                return false;
            coverage -= box.height;
            continue;
        }
        auto next = active.lower_bound(box.y);
        if (next != active.end() && next->first < box.y + box.height)
            return false;
        if (next != active.begin()) {
            const auto& previous = mesh.faces[std::prev(next)->second].box;
            if (previous.y + previous.height > box.y)
                return false;
        }
        active.emplace(box.y, event.index);
        coverage += box.height;
    }
    return active.empty() && previousX == mesh.width;
}
SMeshResult Luminophore::Spatial::buildMesh(const SBoard& board, int64_t width, int64_t height, size_t budget, std::optional<WindowKey> focus) {
    const auto fill = computeOwnership(board, focus, budget);
    if (fill.status == eFillStatus::RESOURCE_LIMIT)
        return {eMeshStatus::RESOURCE_LIMIT, {}, false};
    if (fill.status != eFillStatus::OK || width <= 0 || height <= 0)
        return {};
    SMesh mesh{board.view, width, height, 0, {}};
    mesh.fillFocus = focus;
    FaceID id      = 1;
    for (const auto& rect : fill.rectangles) {
        const auto dx     = rect.logical.origin.x - board.view.origin.x;
        const auto dy     = rect.logical.origin.y - board.view.origin.y;
        const auto x      = scale(dx, width, board.view.columns);
        const auto y      = scale(dy, height, board.view.rows);
        const auto right  = scale(dx + rect.logical.columns, width, board.view.columns);
        const auto bottom = scale(dy + rect.logical.rows, height, board.view.rows);
        if (right == x || bottom == y)
            return {eMeshStatus::UNREPRESENTABLE, {}, false};
        mesh.faces.push_back({id++, rect.logical, {x, y, right - x, bottom - y}, rect.core});
    }
    return {eMeshStatus::OK, std::move(mesh), false};
}
std::optional<std::vector<SRegion>> Luminophore::Spatial::regionsForOwnership(const SMesh& mesh, const SFill& fill) {
    if (!validateMesh(mesh) || fill.status != eFillStatus::OK)
        return std::nullopt;
    std::map<WindowKey, std::vector<SBox>> regions;
    for (const auto& face : mesh.faces) {
        bool found = false;
        for (const auto& rect : fill.rectangles) {
            if (!rect.logical.contains(face.provenance.origin) ||
                !rect.logical.contains({*checkedAdd(face.provenance.origin.x, face.provenance.columns) - 1, *checkedAdd(face.provenance.origin.y, face.provenance.rows) - 1}))
                continue;
            found = true;
            if (rect.owner)
                regions[*rect.owner].push_back(face.box);
            break;
        }
        if (!found)
            return std::nullopt; // Occupancy topology changed: reconcile first.
    }
    std::vector<SRegion> result;
    for (auto& [owner, boxes] : regions) {
        auto       bounds = boxes.front();
        __int128_t area   = 0;
        for (const auto& box : boxes) {
            const auto right  = std::max(bounds.x + bounds.width, box.x + box.width);
            const auto bottom = std::max(bounds.y + bounds.height, box.y + box.height);
            bounds.x          = std::min(bounds.x, box.x);
            bounds.y          = std::min(bounds.y, box.y);
            bounds.width      = right - bounds.x;
            bounds.height     = bottom - bounds.y;
            area += static_cast<__int128_t>(box.width) * box.height;
        }
        if (area != static_cast<__int128_t>(bounds.width) * bounds.height)
            return std::nullopt; // Never expose an L-shaped or disconnected client.
        result.push_back({owner, {bounds}});
    }
    return result;
}
static std::optional<SRect> intersect(const SRect& a, const SRect& b) {
    const auto x      = std::max(a.origin.x, b.origin.x);
    const auto y      = std::max(a.origin.y, b.origin.y);
    const auto r      = std::min(*checkedAdd(a.origin.x, a.columns), *checkedAdd(b.origin.x, b.columns));
    const auto bottom = std::min(*checkedAdd(a.origin.y, a.rows), *checkedAdd(b.origin.y, b.rows));
    if (x >= r || y >= bottom)
        return std::nullopt;
    return SRect{{x, y}, r - x, bottom - y};
}
static SBox projectSubrect(const SRect& whole, const SBox& box, const SRect& sub) {
    const auto dx = sub.origin.x - whole.origin.x;
    const auto dy = sub.origin.y - whole.origin.y;
    const auto x  = box.x + scale(dx, box.width, whole.columns);
    const auto y  = box.y + scale(dy, box.height, whole.rows);
    const auto r  = box.x + scale(dx + sub.columns, box.width, whole.columns);
    const auto b  = box.y + scale(dy + sub.rows, box.height, whole.rows);
    return {x, y, r - x, b - y};
}
SMeshResult Luminophore::Spatial::reconcileMesh(const SMesh& mesh, const SBoard& board, int64_t width, int64_t height, size_t budget, std::optional<WindowKey> focus) {
    if (!validateMesh(mesh) || mesh.revision == UINT64_MAX)
        return {};
    auto defaults = buildMesh(board, width, height, budget, focus);
    if (defaults.status != eMeshStatus::OK)
        return defaults;
    defaults.mesh.revision = mesh.revision + 1;
    const auto fill        = computeOwnership(board, focus, budget);
    const auto common      = intersect(mesh.view, board.view);
    if (!common)
        return defaults; // Entirely new viewport; no old regions to preserve.
    SMesh candidate{board.view, width, height, mesh.revision + 1, {}};
    candidate.fillFocus = focus;
    FaceID     id       = 1;
    const auto append   = [&](const SRect& logical, const SBox& box, bool core) {
        if (!box.valid() || candidate.faces.size() >= budget)
            return false;
        candidate.faces.push_back({id++, logical, box, core});
        return true;
    };
    // New logical bands outside the old view start with default ratios.
    for (const auto& f : defaults.mesh.faces) {
        const auto overlap = intersect(f.provenance, *common);
        if (!overlap) {
            if (!append(f.provenance, f.box, f.core))
                return {eMeshStatus::RESOURCE_LIMIT, mesh, false};
            continue;
        }
        const auto&              p      = f.provenance;
        const auto               right  = *checkedAdd(p.origin.x, p.columns);
        const auto               bottom = *checkedAdd(p.origin.y, p.rows);
        const auto               cr     = *checkedAdd(overlap->origin.x, overlap->columns);
        const auto               cb     = *checkedAdd(overlap->origin.y, overlap->rows);
        const std::vector<SRect> bands{
            {p.origin, p.columns, overlap->origin.y - p.origin.y},
            {{p.origin.x, cb}, p.columns, bottom - cb},
            {{p.origin.x, overlap->origin.y}, overlap->origin.x - p.origin.x, overlap->rows},
            {{cr, overlap->origin.y}, right - cr, overlap->rows},
        };
        for (const auto& band : bands)
            if (band.valid() && !append(band, projectSubrect(p, f.box, band), f.core))
                return {eMeshStatus::RESOURCE_LIMIT, mesh, false};
    }
    struct Retained {
        SRect logical;
        SBox  box;
    };
    std::vector<Retained> retained;
    int64_t               left = mesh.width, top = mesh.height, right = 0, bottom = 0;
    for (const auto& f : mesh.faces) {
        const auto overlap = intersect(f.provenance, *common);
        if (!overlap)
            continue;
        const auto box = projectSubrect(f.provenance, f.box, *overlap);
        retained.push_back({*overlap, box});
        left   = std::min(left, box.x);
        top    = std::min(top, box.y);
        right  = std::max(right, box.x + box.width);
        bottom = std::max(bottom, box.y + box.height);
    }
    const auto target   = projectSubrect(board.view, {0, 0, width, height}, *common);
    bool       feasible = right > left && bottom > top;
    for (const auto& old : retained) {
        if (!feasible)
            break;
        const auto x      = target.x + scale(old.box.x - left, target.width, right - left);
        const auto y      = target.y + scale(old.box.y - top, target.height, bottom - top);
        const auto r      = target.x + scale(old.box.x + old.box.width - left, target.width, right - left);
        const auto b      = target.y + scale(old.box.y + old.box.height - top, target.height, bottom - top);
        auto       mapped = SBox{x, y, r - x, b - y};
        // Re-anchor a newly cropped outer edge; do not reset interior boundaries.
        if (old.logical.origin.x == common->origin.x) {
            mapped.width += mapped.x - target.x;
            mapped.x = target.x;
        }
        if (old.logical.origin.y == common->origin.y) {
            mapped.height += mapped.y - target.y;
            mapped.y = target.y;
        }
        if (*checkedAdd(old.logical.origin.x, old.logical.columns) == *checkedAdd(common->origin.x, common->columns))
            mapped.width = target.x + target.width - mapped.x;
        if (*checkedAdd(old.logical.origin.y, old.logical.rows) == *checkedAdd(common->origin.y, common->rows))
            mapped.height = target.y + target.height - mapped.y;
        for (const auto& rect : fill.rectangles) {
            const auto overlap = intersect(old.logical, rect.logical);
            if (!overlap)
                continue;
            if (candidate.faces.size() >= budget)
                return {eMeshStatus::RESOURCE_LIMIT, mesh, false};
            if (!append(*overlap, projectSubrect(old.logical, mapped, *overlap), rect.core)) {
                feasible = false;
                break;
            }
        }
    }
    if (feasible && validateMesh(candidate) && regionsForOwnership(candidate, fill))
        return {eMeshStatus::OK, std::move(candidate), false};
    // Only the overlapping old-view component is reset. Newly exposed bands
    // already have defaults. Caller must surface reset=true in its result.
    // A focus-only claim change must not reset the user's valid size ratios.
    if (mesh.view == board.view && mesh.width == width && mesh.height == height && regionsForOwnership(mesh, computeOwnership(board, mesh.fillFocus, budget)))
        return {eMeshStatus::OK, mesh, false};
    defaults.reset = true;
    return defaults;
}
