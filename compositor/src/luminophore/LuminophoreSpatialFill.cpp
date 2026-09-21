#include "LuminophoreSpatialFill.hpp"
#include <algorithm>

using namespace Luminophore::Spatial;
SFill Luminophore::Spatial::computeOwnership(const SBoard& board, std::optional<WindowKey> focus, size_t budget) {
    if (!board.view.valid())
        return {};
    const auto&          v = board.view;
    std::vector<int64_t> xs{v.origin.x, *checkedAdd(v.origin.x, v.columns)};
    std::vector<int64_t> ys{v.origin.y, *checkedAdd(v.origin.y, v.rows)};
    for (const auto& [p, key] : board.tiled) {
        if (!key)
            return {};
        if (!v.contains(p))
            continue;
        xs.insert(xs.end(), {p.x, *checkedAdd(p.x, 1)});
        ys.insert(ys.end(), {p.y, *checkedAdd(p.y, 1)});
    }
    for (auto* breaks : {&xs, &ys}) {
        std::ranges::sort(*breaks);
        breaks->erase(std::unique(breaks->begin(), breaks->end()), breaks->end());
    }
    const size_t width  = xs.size() - 1;
    const size_t height = ys.size() - 1;
    if (height > budget / width)
        return {eFillStatus::RESOURCE_LIMIT, {}, 0};
    SFill result{eFillStatus::OK, {}, 0};
    struct SCandidate {
        WindowKey owner;
        SPoint    core;
    };
    std::vector<std::optional<SCandidate>> previous(width);
    for (size_t row = 0; row < height; ++row) {
        std::optional<SCandidate> left;
        for (size_t col = 0; col < width; ++col) {
            const SPoint point{xs[col], ys[row]};
            const auto   at   = board.tiled.find(point);
            const bool   core = at != board.tiled.end();
            // Both routes advance only right/down. Their distance difference is
            // constant throughout this compressed block; retain the original core.
            auto       owner    = core ? std::optional{SCandidate{at->second, point}} : left;
            const auto top      = previous[col];
            const auto distance = [&](const SCandidate& candidate) {
                // Each axis is bounded by the positive int64 view extent. The
                // sum fits uint64 even when signed global coordinates straddle 0.
                return (static_cast<uint64_t>(point.x) - static_cast<uint64_t>(candidate.core.x)) + (static_cast<uint64_t>(point.y) - static_cast<uint64_t>(candidate.core.y));
            };
            if (!core && top && (!owner || distance(*top) < distance(*owner) || (distance(*top) == distance(*owner) && top->owner == focus)))
                owner = top;
            result.rectangles.push_back({{point, xs[col + 1] - xs[col], ys[row + 1] - ys[row]}, owner ? std::optional{owner->owner} : std::nullopt, core});
            previous[col] = owner;
            left          = owner;
            ++result.visited;
        }
    }
    // The propagation above ranks competing claims; it is not the final shape.
    // Evaluate every anchored rectangular extent jointly in both axes. Claims are
    // disjoint, so selecting each owner's largest rectangle cannot steal a core.
    // Equal-area candidates prefer width, independently of key/insertion order.
    std::map<WindowKey, SRect> selected;
    for (const auto& [point, key] : board.tiled) {
        if (!v.contains(point))
            continue;
        const size_t cx    = std::lower_bound(xs.begin(), xs.end(), point.x) - xs.begin();
        const size_t cy    = std::lower_bound(ys.begin(), ys.end(), point.y) - ys.begin();
        size_t       right = width;
        __int128_t   area  = 0;
        SRect        best{point, 1, 1};
        for (size_t row = cy; row < height; ++row) {
            size_t end = cx;
            while (end < right && result.rectangles[row * width + end].owner == key)
                ++end;
            right = end;
            if (right == cx)
                break;
            const auto columns = xs[right] - point.x, rows = ys[row + 1] - point.y;
            const auto candidateArea = static_cast<__int128_t>(columns) * rows;
            if (candidateArea > area || (candidateArea == area && columns > best.columns)) {
                area = candidateArea;
                best = {point, columns, rows};
            }
        }
        selected[key] = best;
    }
    for (auto& rect : result.rectangles)
        if (rect.owner && !selected.at(*rect.owner).contains(rect.logical.origin))
            rect.owner.reset();
    // Released claims are vacant, not reservations. Recover them without ever
    // shrinking or pivoting a selected rectangle. Distance order is independent
    // of the contested point: nearer cores have larger x+y. On equal distance,
    // prefer focus, then the left core, exactly as in the propagation above.
    std::vector<SCandidate> recovery;
    for (const auto& [point, key] : board.tiled)
        if (v.contains(point))
            recovery.push_back({key, point});
    std::ranges::sort(recovery, [&](const SCandidate& a, const SCandidate& b) {
        const auto da = static_cast<__int128_t>(a.core.x) + a.core.y;
        const auto db = static_cast<__int128_t>(b.core.x) + b.core.y;
        if (da != db)
            return da > db;
        if ((a.owner == focus) != (b.owner == focus))
            return a.owner == focus;
        return a.core.x < b.core.x;
    });
    // A single pass suffices: recovery only consumes vacancies, so a candidate
    // rejected now cannot become feasible after another window expands.
    for (const auto& candidate : recovery) {
        const auto&  point   = candidate.core;
        const auto   key     = candidate.owner;
        const auto   initial = selected.at(key);
        auto         best    = initial;
        auto         area    = static_cast<__int128_t>(best.columns) * best.rows;
        const size_t cx      = std::lower_bound(xs.begin(), xs.end(), point.x) - xs.begin();
        const size_t cy      = std::lower_bound(ys.begin(), ys.end(), point.y) - ys.begin();
        size_t       right   = width;
        for (size_t row = cy; row < height; ++row) {
            size_t end = cx;
            while (end < right) {
                const auto owner = result.rectangles[row * width + end].owner;
                if (owner && owner != key)
                    break;
                ++end;
            }
            right              = end;
            const auto columns = xs[right] - point.x, rows = ys[row + 1] - point.y;
            if (columns < initial.columns)
                break;
            if (rows < initial.rows)
                continue;
            const auto candidateArea = static_cast<__int128_t>(columns) * rows;
            if (candidateArea > area || (candidateArea == area && columns > best.columns)) {
                area = candidateArea;
                best = {point, columns, rows};
            }
        }
        for (size_t row = cy; row < height && ys[row] - point.y < best.rows; ++row)
            for (size_t col = cx; col < width && xs[col] - point.x < best.columns; ++col)
                result.rectangles[row * width + col].owner = key;
    }
    return result;
}
