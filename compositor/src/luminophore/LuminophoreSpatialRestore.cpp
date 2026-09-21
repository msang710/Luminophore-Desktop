#include "LuminophoreSpatialModel.hpp"
#include "LuminophoreSpatialProjection.hpp"
#include <algorithm>
#include <limits>

namespace NS = Luminophore::Spatial;

// This bypasses SCreate deliberately: replay must not auto-expand the restored
// view or move a preserved, later window back into its visible ownership mesh.
bool CLuminophoreSpatialModel::restoreSpatial(const SLuminophoreSpatialSnapshot& saved, const std::set<LuminophoreWindowKey>& retained, const std::set<LuminophoreWindowKey>& excluded) {
    if (!m_independent || !saved.independent || m_revision == UINT64_MAX)
        return false;
    const auto& old  = *saved.independent;
    const auto& live = *m_independent;
    auto        next = *this;
    auto&       out  = *next.m_independent;

    // Connector identity survives output-object replacement. Physical topology
    // itself is never replayed; an unavailable destination rejects the operation.
    for (const auto& [output, board] : old.bindings) {
        const auto historical = old.outputs.find(output);
        if (historical == old.outputs.end())
            return false;
        const auto connected = std::ranges::find_if(live.outputs, [&](const auto& item) { return item.second.name == historical->second.name; });
        if (connected == live.outputs.end() || live.bindings.at(connected->first) != board)
            return false;
    }
    std::set<LuminophoreWindowKey> currentKeys;
    for (const auto& [board, b] : live.state.boards)
        for (const auto& [p, key] : b.tiled)
            currentKeys.insert(key);
    for (const auto& [key, board] : live.floatingBoards)
        currentKeys.insert(key);
    // Mapped minimized windows have no active model placement. Their separately
    // checked lifetime permits reinstating the saved placement without relaunch.
    for (const auto& p : saved.tiled)
        if (retained.contains(p.key))
            currentKeys.insert(p.key);
    for (const auto& p : saved.floating)
        if (retained.contains(p.key))
            currentKeys.insert(p.key);
    for (const auto key : excluded)
        currentKeys.erase(key);
    std::set<LuminophoreWindowKey> restoreKeys;
    for (const auto& p : saved.tiled)
        if (retained.contains(p.key) && currentKeys.contains(p.key))
            restoreKeys.insert(p.key);
    for (const auto& p : saved.floating)
        if (retained.contains(p.key) && currentKeys.contains(p.key))
            restoreKeys.insert(p.key);

    for (const auto& [id, b] : old.state.boards) {
        if (!out.state.boards.contains(id))
            return false;
        auto replacement = b;
        replacement.tiled.clear();
        out.state.boards[id] = std::move(replacement);
    }
    // Clear placements on all boards before inserting old placements, so cross
    // monitor moves cannot leave a duplicate key on a newly connected board.
    for (auto& [id, b] : out.state.boards)
        b.tiled.clear();
    out.floatingBoards.clear();
    next.m_floatingHosts.clear();
    next.m_floatingBoxes.clear();
    std::map<uint64_t, std::set<NS::SPoint>> occupied;
    for (const auto& [id, b] : old.state.boards)
        for (const auto& [p, key] : b.tiled)
            if (restoreKeys.contains(key)) {
                out.state.boards.at(id).tiled[p] = key;
                occupied[id].insert(p);
            }
    for (const auto& p : saved.floating) {
        if (!restoreKeys.contains(p.key))
            continue;
        const auto board = old.floatingBoards.find(p.key);
        if (board == old.floatingBoards.end() || !out.state.boards.contains(board->second) || !p.localBox.valid())
            return false;
        out.floatingBoards[p.key]   = board->second;
        next.m_floatingHosts[p.key] = p.host;
        next.m_floatingBoxes[p.key] = p.localBox;
        occupied[board->second].insert({p.host.x, p.host.y});
    }
    // Deterministic, bounded vacancy search. Jump to a view edge rather than
    // walking an arbitrarily large coordinate space, then scan at most N+1 slots.
    const auto vacant = [&](uint64_t id, NS::SPoint origin) -> std::optional<NS::SPoint> {
        const auto& view = out.state.boards.at(id).view;
        if (!view.contains(origin) && !occupied[id].contains(origin))
            return origin;
        const auto              right  = NS::checkedAdd(view.origin.x, view.columns);
        const auto              bottom = NS::checkedAdd(view.origin.y, view.rows);
        const auto              left   = NS::checkedAdd(view.origin.x, -1);
        const auto              top    = NS::checkedAdd(view.origin.y, -1);
        std::vector<NS::SPoint> candidates;
        if (right)
            candidates.push_back({*right, origin.y});
        if (bottom)
            candidates.push_back({origin.x, *bottom});
        if (left)
            candidates.push_back({*left, origin.y});
        if (top)
            candidates.push_back({origin.x, *top});
        std::stable_sort(candidates.begin(), candidates.end(), [&](auto a, auto b) {
            const auto distance = [&](auto p) { return std::abs(static_cast<long double>(p.x) - origin.x) + std::abs(static_cast<long double>(p.y) - origin.y); };
            return distance(a) < distance(b);
        });
        for (auto p : candidates)
            for (size_t n = 0; n <= currentKeys.size(); ++n) {
                if (!view.contains(p) && !occupied[id].contains(p))
                    return p;
                // Scan vertically on vertical edges, horizontally on horizontal edges.
                const bool vertical = p.x < view.origin.x || (right && p.x >= *right);
                auto       value    = NS::checkedAdd(vertical ? p.y : p.x, 1);
                if (!value)
                    break;
                (vertical ? p.y : p.x) = *value;
            }
        return std::nullopt;
    };
    for (const auto& [id, b] : live.state.boards)
        for (const auto& [p, key] : b.tiled) {
            if (restoreKeys.contains(key) || excluded.contains(key))
                continue;
            const auto point = vacant(id, p);
            if (!point)
                return false;
            out.state.boards.at(id).tiled[*point] = key;
            occupied[id].insert(*point);
        }
    for (const auto& [key, id] : live.floatingBoards) {
        if (restoreKeys.contains(key) || excluded.contains(key))
            continue;
        const auto host  = m_floatingHosts.at(key);
        const auto point = vacant(id, {host.x, host.y});
        if (!point)
            return false;
        out.floatingBoards[key]   = id;
        next.m_floatingHosts[key] = {point->x, point->y};
        next.m_floatingBoxes[key] = m_floatingBoxes.at(key);
        occupied[id].insert(*point);
    }
    out.displaced.clear();
    for (const auto& [key, where] : old.displaced)
        if (restoreKeys.contains(key))
            out.displaced[key] = where;
    out.state.focus = old.state.focus && restoreKeys.contains(*old.state.focus) ? old.state.focus : live.state.focus;
    if (out.state.focus && !currentKeys.contains(*out.state.focus))
        out.state.focus.reset();
    out.state.revision       = m_revision + 1;
    out.focusRevision        = live.focusRevision + 1;
    out.selectedOutput       = live.selectedOutput;
    next.m_presentationMode  = saved.presentationMode;
    next.m_desktopReturnMode = saved.desktopReturnMode;
    next.m_wideKey           = saved.wideKey && restoreKeys.contains(*saved.wideKey) ? saved.wideKey : std::nullopt;
    if (!next.m_wideKey) {
        if (next.m_presentationMode == eLuminophorePresentationMode::WIDE)
            next.m_presentationMode = eLuminophorePresentationMode::NORMAL;
        next.m_desktopReturnMode = eLuminophorePresentationMode::NORMAL;
    }
    const auto repaired = next.occupancyState(true);
    if (!repaired)
        return false;
    next.assignOccupancy(*repaired);
    if (!next.occupancyState())
        return false;
    for (const auto& [output, id] : out.bindings) {
        const auto& geometry = out.outputs.at(output);
        const auto& board    = out.state.boards.at(id);
        const auto  prior    = old.meshes.find(id);
        // Pixel split geometry cannot be restored exactly at another resolution.
        if (prior != old.meshes.end() && (prior->second.width != geometry.width || prior->second.height != geometry.height))
            return false;
        auto mesh = prior == old.meshes.end() ? NS::buildMesh(board, geometry.width, geometry.height, 1'000'000, out.state.focus) :
                                                NS::reconcileMesh(prior->second, board, geometry.width, geometry.height, 1'000'000, prior->second.fillFocus);
        if (mesh.status != NS::eMeshStatus::OK)
            return false;
        mesh.mesh.revision = m_revision + 1;
        out.meshes[id]     = std::move(mesh.mesh);
    }
    // Legacy overlapping frames may need a new logical host. Preserve their
    // physical floating rectangle when both old and new hosts are visible.
    for (const auto& placement : saved.floating) {
        if (!restoreKeys.contains(placement.key) || !next.m_floatingHosts.contains(placement.key) || next.m_floatingHosts.at(placement.key) == placement.host)
            continue;
        const auto board = out.floatingBoards.at(placement.key);
        for (const auto& [output, id] : out.bindings) {
            if (id != board)
                continue;
            const auto&               o = out.outputs.at(output);
            const SLuminophorePhysicalOutput extent{output, o.name, {o.x, o.y, o.width, o.height}};
            const auto                oldHost = CLuminophoreSpatialProjection::hostBox(saved, placement.host, extent);
            const auto                newHost = CLuminophoreSpatialProjection::hostBox(next.snapshot(), next.m_floatingHosts.at(placement.key), extent);
            if (!oldHost || !newHost)
                continue;
            const auto box   = CLuminophoreSpatialProjection::denormalize(placement.localBox, *oldHost);
            const auto local = box ? CLuminophoreSpatialProjection::normalize(*box, *newHost) : std::nullopt;
            if (!local)
                return false;
            next.m_floatingBoxes[placement.key] = *local;
        }
    }
    next.syncIndependentCaches();
    next.m_revision = m_revision + 1;
    *this           = std::move(next);
    return true;
}
