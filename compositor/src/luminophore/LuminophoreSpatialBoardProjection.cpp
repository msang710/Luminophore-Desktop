#include "LuminophoreSpatialProjection.hpp"
#include <algorithm>
#include <set>
#include <cmath>

namespace NS = Luminophore::Spatial;
static SLuminophorePhysicalBox fullBox(const SLuminophorePhysicalOutput& o) {
    return o.logicalBox.width > 0 && o.logicalBox.height > 0 ? o.logicalBox : o.box;
}
static bool wideNeighbors(const SLuminophorePhysicalOutput& a, const SLuminophorePhysicalOutput& b) {
    const auto x = fullBox(a), y = fullBox(b);
    if (x.width != y.width || x.height != y.height)
        return false;
    const bool horizontal = (int64_t(x.x) + x.width == y.x || int64_t(y.x) + y.width == x.x) && std::max(x.y, y.y) < std::min(int64_t(x.y) + x.height, int64_t(y.y) + y.height);
    const bool vertical   = (int64_t(x.y) + x.height == y.y || int64_t(y.y) + y.height == x.y) && std::max(x.x, y.x) < std::min(int64_t(x.x) + x.width, int64_t(y.x) + y.width);
    return horizontal || vertical;
}
std::optional<SLuminophorePhysicalBox> CLuminophoreSpatialProjection::hostBox(const SLuminophoreSpatialSnapshot& snapshot, SLuminophoreBoardPoint point, const SLuminophorePhysicalOutput& output) {
    if (!snapshot.independent)
        return std::nullopt;
    const auto& state   = *snapshot.independent;
    const auto  binding = state.bindings.find(output.id);
    if (binding == state.bindings.end())
        return std::nullopt;
    const auto mesh = state.meshes.find(binding->second);
    if (mesh == state.meshes.end())
        return std::nullopt;
    for (const auto& face : mesh->second.faces) {
        if (!face.provenance.contains({point.x, point.y}))
            continue;
        const auto dx = int64_t{point.x} - face.provenance.origin.x;
        const auto dy = int64_t{point.y} - face.provenance.origin.y;
        const auto x0 = std::llround(face.box.x + double(face.box.width) * dx / face.provenance.columns);
        const auto y0 = std::llround(face.box.y + double(face.box.height) * dy / face.provenance.rows);
        const auto x1 = std::llround(face.box.x + double(face.box.width) * (dx + 1) / face.provenance.columns);
        const auto y1 = std::llround(face.box.y + double(face.box.height) * (dy + 1) / face.provenance.rows);
        if (x1 <= x0 || y1 <= y0)
            return std::nullopt;
        return SLuminophorePhysicalBox{.x = output.box.x + int(x0), .y = output.box.y + int(y0), .width = int(x1 - x0), .height = int(y1 - y0)};
    }
    return std::nullopt;
}

std::optional<SLuminophoreProjectionPlan> CLuminophoreSpatialProjection::planIndependent(const SLuminophoreSpatialSnapshot& snapshot, uint64_t revision,
                                                                           const std::vector<SLuminophorePhysicalOutput>& outputs) {
    const auto& s = *snapshot.independent;
    if (snapshot.outputTopologyRevision != revision || s.bindings.size() != outputs.size())
        return std::nullopt;
    SLuminophoreProjectionPlan result{.modelRevision = snapshot.revision, .topologyRevision = revision, .presentationMode = snapshot.presentationMode};
    std::set<uint64_t>  seen, wide;
    uint64_t            wideBoard = 0;
    for (const auto& [id, b] : s.state.boards)
        for (const auto& [point, key] : b.tiled)
            if (snapshot.wideKey == key)
                wideBoard = id;
    if (snapshot.presentationMode == eLuminophorePresentationMode::WIDE) {
        if (!wideBoard)
            return std::nullopt;
        for (const auto& [out, id] : s.bindings)
            if (id == wideBoard)
                wide.insert(out);
        bool added = true;
        while (added) {
            added = false;
            for (const auto& a : outputs)
                if (wide.contains(a.id))
                    for (const auto& b : outputs)
                        if (!wide.contains(b.id) && wideNeighbors(a, b)) {
                            wide.insert(b.id);
                            added = true;
                        }
        }
    }
    for (const auto& [id, b] : s.state.boards)
        for (const auto& [point, key] : b.tiled)
            result.windows.push_back({.key = key, .point = {point.x, point.y}});
    for (const auto& p : snapshot.floating)
        result.windows.push_back({.key = p.key, .point = p.host, .floating = true});
    for (const auto& o : outputs) {
        if (!o.id || o.box.width <= 0 || o.box.height <= 0 || !seen.insert(o.id).second || !s.bindings.contains(o.id))
            return std::nullopt;
        const auto  id = s.bindings.at(o.id);
        const auto& b  = s.state.boards.at(id);
        const auto  mi = s.meshes.find(id);
        if (mi == s.meshes.end())
            return std::nullopt;
        const auto& mesh    = mi->second;
        const auto  regions = NS::regionsForOwnership(mesh, NS::computeOwnership(b, mesh.fillFocus));
        if (!regions || mesh.width != o.box.width || mesh.height != o.box.height)
            return std::nullopt;
        // Compressed faces keep sparse, distant coordinates bounded in work and memory.
        for (const auto& f : mesh.faces)
            result.cells.push_back(
                {.point    = {f.provenance.origin.x, f.provenance.origin.y},
                 .outputID = o.id,
                 .box      = {o.box.x + static_cast<int>(f.box.x), o.box.y + static_cast<int>(f.box.y), static_cast<int>(f.box.width), static_cast<int>(f.box.height)}});
        if (snapshot.presentationMode == eLuminophorePresentationMode::DESKTOP)
            continue;
        if (wide.contains(o.id)) {
            auto w = std::ranges::find(result.windows, *snapshot.wideKey, &SLuminophoreProjectedWindow::key);
            w->fragments.push_back({.key = w->key, .point = w->point, .outputID = o.id, .box = fullBox(o)});
            w->visible = true;
            continue;
        }
        for (const auto& r : *regions) {
            auto w = std::ranges::find(result.windows, r.owner, &SLuminophoreProjectedWindow::key);
            if (w == result.windows.end())
                return std::nullopt;
            for (const auto& box : r.boxes)
                w->fragments.push_back({.key      = w->key,
                                        .point    = w->point,
                                        .outputID = o.id,
                                        .box = {o.box.x + static_cast<int>(box.x), o.box.y + static_cast<int>(box.y), static_cast<int>(box.width), static_cast<int>(box.height)}});
            w->visible         = !w->fragments.empty();
            w->primaryOutputID = o.id;
        }
        for (const auto& p : snapshot.floating) {
            const auto bi = s.floatingBoards.find(p.key);
            if (bi == s.floatingBoards.end() || bi->second != id || !b.view.contains({p.host.x, p.host.y}))
                continue;
            const auto face = std::ranges::find_if(mesh.faces, [&](const auto& f) { return f.provenance.contains({p.host.x, p.host.y}); });
            if (face == mesh.faces.end())
                continue;
            const auto host = hostBox(snapshot, p.host, o);
            if (!host)
                continue;
            const auto box = denormalize(p.localBox, *host);
            if (!box)
                return std::nullopt;
            auto      w = std::ranges::find(result.windows, p.key, &SLuminophoreProjectedWindow::key);
            const int l = std::max(box->x, o.box.x), t = std::max(box->y, o.box.y), r = std::min(box->x + box->width, o.box.x + o.box.width),
                      bottom = std::min(box->y + box->height, o.box.y + o.box.height);
            if (r > l && bottom > t) {
                w->fragments.push_back({.key = p.key, .point = p.host, .outputID = o.id, .box = {l, t, r - l, bottom - t}});
                w->visible         = true;
                w->clientBox       = *box;
                w->primaryOutputID = o.id;
            }
        }
    }
    std::ranges::sort(result.windows, {}, &SLuminophoreProjectedWindow::key);
    return result;
}
