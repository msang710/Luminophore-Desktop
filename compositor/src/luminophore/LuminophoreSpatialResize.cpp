#include "LuminophoreSpatialResize.hpp"
#include <algorithm>
#include <climits>
#include <map>
#include <tuple>

using namespace Luminophore::Spatial;

SResizeResult Luminophore::Spatial::solveWindowResize(const SMesh& mesh, const SFill& fill, WindowKey window, const SResizeRequest& request,
                                               const std::map<WindowKey, SPoint>& clientMinimums) {
    const auto fail = [&](eResizeStatus status) { return SResizeResult{status, mesh, 0}; };
    if (request.expectedRevision != mesh.revision)
        return fail(eResizeStatus::STALE);
    const auto regions = regionsForOwnership(mesh, fill);
    if (static_cast<unsigned>(request.side) > static_cast<unsigned>(eSide::BOTTOM) || !window || !regions || request.minimum < 1 || request.delta == INT64_MIN ||
        mesh.revision == UINT64_MAX)
        return fail(eResizeStatus::INVALID);
    if (mesh.faces.size() > request.faceBudget)
        return fail(eResizeStatus::RESOURCE_LIMIT);
    const auto ownerOf = [&](const SFace& face) -> std::optional<WindowKey> {
        for (const auto& r : fill.rectangles)
            if (r.logical.contains(face.provenance.origin))
                return r.owner;
        return std::nullopt;
    };
    const auto held     = std::ranges::find(mesh.faces, request.face, &SFace::id);
    const auto selected = std::ranges::find(*regions, window, &SRegion::owner);
    if (held == mesh.faces.end() || selected == regions->end() || ownerOf(*held) != window)
        return fail(eResizeStatus::INVALID);
    const bool vertical = request.side == eSide::TOP || request.side == eSide::BOTTOM;
    const bool low      = request.side == eSide::LEFT || request.side == eSide::TOP;
    const auto edgeOf   = [&](const SBox& b) { return vertical ? b.y + (low ? 0 : b.height) : b.x + (low ? 0 : b.width); };
    if (edgeOf(held->box) != edgeOf(selected->boxes.front()))
        return fail(eResizeStatus::INVALID); // Logical seams inside a window are not handles.
    if (!request.delta)
        return fail(eResizeStatus::NO_CHANGE);
    const bool    negative = request.delta < 0;
    const int64_t extent   = vertical ? mesh.height : mesh.width;
    const auto    orient   = [&](SBox b) {
        if (vertical) {
            std::swap(b.x, b.y);
            std::swap(b.width, b.height);
        }
        if (negative)
            b.x = extent - b.x - b.width;
        return b;
    };
    struct Rect {
        SBox                     box;
        std::optional<WindowKey> owner;
        size_t                   left = 0, right = 0;
        int64_t                  minimum = 1;
    };
    std::vector<Rect>           rects;
    std::map<WindowKey, size_t> indices;
    for (const auto& r : *regions) {
        indices[r.owner] = rects.size();
        auto    b        = orient(r.boxes.front());
        int64_t minimum  = request.minimum;
        if (const auto it = clientMinimums.find(r.owner); it != clientMinimums.end()) {
            if (it->second.x < 1 || it->second.y < 1)
                return fail(eResizeStatus::INVALID);
            minimum = std::max(minimum, vertical ? it->second.y : it->second.x);
        }
        rects.push_back({b, r.owner, 0, 0, std::min(minimum, b.width)});
    }
    std::vector<size_t> faceRect;
    for (const auto& face : mesh.faces) {
        if (const auto owner = ownerOf(face))
            faceRect.push_back(indices.at(*owner));
        else {
            faceRect.push_back(rects.size());
            rects.push_back({orient(face.box), std::nullopt});
        }
    }
    // Join only overlapping boundary segments. A T junction moves the complete
    // neighboring rectangle edge; independent rows meeting at one point stay independent.
    struct Edge {
        int64_t x, begin, end;
        size_t  rect;
        bool    right;
    };
    std::vector<Edge> edges;
    for (size_t i = 0; i < rects.size(); ++i) {
        const auto& b = rects[i].box;
        edges.push_back({b.x, b.y, b.y + b.height, i, false});
        edges.push_back({b.x + b.width, b.y, b.y + b.height, i, true});
    }
    std::ranges::sort(edges, [](const auto& a, const auto& b) { return std::tie(a.x, a.begin, a.end) < std::tie(b.x, b.begin, b.end); });
    struct Wall {
        int64_t             position, end, limit;
        std::vector<size_t> children;
    };
    std::vector<Wall> walls;
    for (const auto& edge : edges) {
        if (walls.empty() || walls.back().position != edge.x || walls.back().end <= edge.begin)
            walls.push_back({edge.x, edge.end, extent, {}});
        else
            walls.back().end = std::max(walls.back().end, edge.end);
        (edge.right ? rects[edge.rect].right : rects[edge.rect].left) = walls.size() - 1;
    }
    // Reserve at least one pixel per retained internal seam, not 100px per cell.
    std::vector<std::vector<int64_t>> cuts(rects.size());
    for (size_t i = 0; i < mesh.faces.size(); ++i) {
        const auto box = orient(mesh.faces[i].box);
        auto&      c   = cuts[faceRect[i]];
        c.insert(c.end(), {box.x, box.x + box.width});
    }
    for (size_t i = 0; i < rects.size(); ++i) {
        auto& c = cuts[i];
        std::ranges::sort(c);
        c.erase(std::unique(c.begin(), c.end()), c.end());
        rects[i].minimum = std::max(rects[i].minimum, static_cast<int64_t>(c.size() - 1));
        walls[rects[i].left].children.push_back(i);
    }
    for (size_t i = walls.size(); i-- > 0;)
        for (auto child : walls[i].children)
            walls[i].limit = std::min(walls[i].limit, walls[rects[child].right].limit - rects[child].minimum);
    const auto&  root = rects[indices.at(window)];
    const size_t wall = (low != negative) ? root.left : root.right;
    if (walls[wall].position == 0 || walls[wall].position == extent)
        return fail(eResizeStatus::NO_CHANGE);
    const int64_t amount = std::min(negative ? -request.delta : request.delta, walls[wall].limit - walls[wall].position);
    if (amount <= 0)
        return fail(eResizeStatus::NO_CHANGE);
    std::vector<int64_t> positions;
    for (const auto& w : walls)
        positions.push_back(w.position);
    positions[wall] += amount;
    for (size_t i = wall; i < walls.size(); ++i)
        for (auto child : walls[i].children) {
            const auto& r      = rects[child];
            positions[r.right] = std::max(positions[r.right], positions[i] + r.minimum);
        }
    std::vector<std::map<int64_t, int64_t>> mapped(rects.size());
    for (size_t i = 0; i < rects.size(); ++i) {
        const auto& r        = rects[i];
        const auto  length   = positions[r.right] - positions[r.left];
        int64_t     previous = positions[r.left] - 1;
        for (size_t j = 0; j < cuts[i].size(); ++j) {
            const auto scaled     = positions[r.left] + static_cast<int64_t>(static_cast<__int128_t>(cuts[i][j] - r.box.x) * length / r.box.width);
            const auto value      = std::clamp(scaled, previous + 1, positions[r.right] - static_cast<int64_t>(cuts[i].size() - 1 - j));
            mapped[i][cuts[i][j]] = value;
            previous              = value;
        }
    }
    auto result = mesh;
    for (size_t i = 0; i < result.faces.size(); ++i) {
        auto       b     = orient(mesh.faces[i].box);
        const auto right = mapped[faceRect[i]].at(b.x + b.width);
        b.x              = mapped[faceRect[i]].at(b.x);
        b.width          = right - b.x;
        if (negative)
            b.x = extent - b.x - b.width;
        if (vertical) {
            std::swap(b.x, b.y);
            std::swap(b.width, b.height);
        }
        result.faces[i].box = b;
    }
    if (!validateMesh(result) || !regionsForOwnership(result, fill))
        return fail(eResizeStatus::INVALID);
    ++result.revision;
    return {eResizeStatus::APPLIED, std::move(result), negative ? -amount : amount};
}
