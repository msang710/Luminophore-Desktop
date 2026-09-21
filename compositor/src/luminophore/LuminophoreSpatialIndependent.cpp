#include "LuminophoreSpatialModel.hpp"
#include "LuminophoreSpatialTransaction.hpp"
#include "LuminophoreSpatialResize.hpp"
#include "LuminophoreSpatialProjection.hpp"
#include "LuminophoreSpatialPlacementPolicy.hpp"
#include <algorithm>
#include <limits>
#include <climits>
#include <type_traits>
#include <set>

namespace NS = Luminophore::Spatial;
static std::optional<NS::SPoint> freeCoordinate(const std::map<NS::SPoint, NS::WindowKey>& occupied, NS::SPoint origin) {
    // Search Manhattan rings; all preceding valid positions were occupied, so
    // this is bounded by the number of windows, not the coordinate-space span.
    for (size_t radius = 0; radius <= occupied.size() && radius <= INT64_MAX; ++radius) {
        const auto r = static_cast<int64_t>(radius);
        for (int64_t dx = -r; dx <= r; ++dx) {
            const auto x = NS::checkedAdd(origin.x, dx);
            if (!x)
                continue;
            const auto dy = r - std::abs(dx);
            for (const auto offset : {-dy, dy}) {
                const auto y = NS::checkedAdd(origin.y, offset);
                if (y && !occupied.contains({*x, *y}))
                    return NS::SPoint{*x, *y};
            }
        }
    }
    return std::nullopt;
}

// The combined table is derived for each transaction, never a second persisted
// owner of placement. Rendering continues to consume only the tiled partition.
std::optional<NS::SState> CLuminophoreSpatialModel::occupancyState(bool repair) const {
    if (!m_independent)
        return std::nullopt;
    auto state = m_independent->state;
    for (const auto& [key, board] : m_independent->floatingBoards) {
        if (!state.boards.contains(board) || !m_floatingHosts.contains(key))
            return std::nullopt;
        const auto host = m_floatingHosts.at(key);
        NS::SPoint point{host.x, host.y};
        auto&      occupied = state.boards.at(board).tiled;
        if (repair) {
            const auto free = freeCoordinate(occupied, point);
            if (!free)
                return std::nullopt;
            point = *free;
        }
        if (!occupied.emplace(point, key).second)
            return std::nullopt;
    }
    return NS::validate(state) ? std::optional{state} : std::nullopt;
}

void CLuminophoreSpatialModel::assignOccupancy(NS::SState state) {
    auto&      independent = *m_independent;
    const auto floating    = independent.floatingBoards;
    independent.floatingBoards.clear();
    for (auto& [board, b] : state.boards) {
        for (auto it = b.tiled.begin(); it != b.tiled.end();) {
            if (!floating.contains(it->second)) {
                ++it;
                continue;
            }
            independent.floatingBoards[it->second] = board;
            m_floatingHosts[it->second]            = {it->first.x, it->first.y};
            it                                     = b.tiled.erase(it);
        }
    }
    for (const auto& [key, board] : floating)
        if (!independent.floatingBoards.contains(key))
            eraseFloating(key);
    independent.state = std::move(state);
}

bool CLuminophoreSpatialModel::independentBoards() const {
    return m_independent.has_value();
}
std::optional<uint64_t> CLuminophoreSpatialModel::boardFor(LuminophoreWindowKey key) const {
    if (!m_independent)
        return std::nullopt;
    for (const auto& [id, b] : m_independent->state.boards)
        for (const auto& [p, k] : b.tiled)
            if (k == key)
                return id;
    const auto it = m_independent->floatingBoards.find(key);
    return it == m_independent->floatingBoards.end() ? std::nullopt : std::optional{it->second};
}
std::optional<uint64_t> CLuminophoreSpatialModel::outputFor(LuminophoreWindowKey key) const {
    const auto board = boardFor(key);
    if (board)
        for (const auto& [output, id] : m_independent->bindings)
            if (id == *board)
                return output;
    return std::nullopt;
}
void CLuminophoreSpatialModel::syncIndependentCaches() {
    auto& s = *m_independent;
    m_floatingByPoint.clear();
    for (const auto& [key, host] : m_floatingHosts)
        m_floatingByPoint[host].push_back(key);
    m_tiledCoordinates.clear();
    m_tiled.clear();
    m_outputViews.clear();
    for (const auto& [id, b] : s.state.boards)
        for (const auto& [p, k] : b.tiled)
            m_tiledCoordinates[k] = {p.x, p.y};
    for (const auto& [output, id] : s.bindings) {
        const auto&                         b = s.state.boards.at(id);
        std::optional<LuminophoreWindowKey> anchor;
        if (b.lastAnchor && b.view.contains(*b.lastAnchor)) {
            const auto found = b.tiled.find(*b.lastAnchor);
            if (found != b.tiled.end())
                anchor = found->second;
        }
        m_outputViews.push_back({.outputID  = output,
                                 .rect      = {{b.view.origin.x, b.view.origin.y}, static_cast<int>(b.view.columns), static_cast<int>(b.view.rows)},
                                 .anchorKey = anchor,
                                 .boardID   = id});
    }
    const auto selected = std::ranges::find(m_outputViews, s.selectedOutput, &SLuminophoreOutputView::outputID);
    if (selected != m_outputViews.end())
        m_view = selected->rect;
    else if (!m_outputViews.empty())
        m_view = m_outputViews.front().rect;
    m_extent = {std::max(1, m_view.columns), std::max(1, m_view.rows)}; // Display viewport, never capacity.
}
static int64_t distance(const SLuminophoreOutputGeometry& a, const SLuminophoreOutputGeometry& b) {
    const int64_t dx = std::max<int64_t>({0, int64_t(a.x) - b.x - b.width, int64_t(b.x) - a.x - a.width});
    const int64_t dy = std::max<int64_t>({0, int64_t(a.y) - b.y - b.height, int64_t(b.y) - a.y - a.height});
    return dx + dy;
}
SLuminophoreSpatialTransactionResult CLuminophoreSpatialModel::transactIndependent(const SLuminophoreSpatialCommand& command) {
    const auto before = snapshot();
    const auto fail   = [&](eLuminophoreSpatialTransactionStatus status) { return SLuminophoreSpatialTransactionResult{.status = status, .snapshot = before}; };
    if (command.expectedRevision != m_revision)
        return fail(eLuminophoreSpatialTransactionStatus::STALE_REVISION);
    const bool staleTopology = std::visit(
        [&](const auto& p) {
            if constexpr (requires {
                              p.expectedTopologyRevision;
                              p.outputID;
                          })
                return p.outputID && p.expectedTopologyRevision != m_outputTopologyRevision;
            else
                return false;
        },
        command.payload);
    if (staleTopology)
        return fail(eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY);
    if (m_revision == UINT64_MAX)
        return fail(eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    auto  next = *this;
    auto& s    = *next.m_independent;
    if (const auto occupied = next.occupancyState(true))
        next.assignOccupancy(*occupied);
    else
        return fail(eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    bool                    focusUpdate = false;
    auto                    moveResult  = eLuminophoreSpatialMoveResult::REJECTED;
    std::optional<uint64_t> nextFocus;
    const auto              apply = [&](const NS::Command& cmd) {
        const auto occupied = next.occupancyState();
        if (!occupied)
            return false;
        auto r = NS::transact(*occupied, occupied->revision, cmd);
        if (r.status != NS::eStatus::APPLIED && r.status != NS::eStatus::NO_CHANGE)
            return false;
        next.assignOccupancy(std::move(r.state));
        return true;
    };
    const auto boardAt = [&](uint64_t output) -> std::optional<uint64_t> {
        if (!output)
            output = s.selectedOutput;
        const auto it = s.bindings.find(output);
        return it == s.bindings.end() ? std::nullopt : std::optional{it->second};
    };
    const auto moveTo = [&](uint64_t key, uint64_t output, NS::SPoint point) {
        const auto board = boardAt(output);
        if (!board)
            return false;
        const auto occupied = next.occupancyState();
        if (!occupied)
            return false;
        const auto& target = occupied->boards.at(*board).tiled;
        moveResult         = target.contains(point) && target.at(point) != key ? eLuminophoreSpatialMoveResult::SWAPPED : eLuminophoreSpatialMoveResult::MOVED;
        s.displaced.erase(key);
        if (target.contains(point))
            s.displaced.erase(target.at(point));
        return apply(NS::SMove{key, *board, point});
    };
    const auto addFloating = [&](LuminophoreWindowKey key, uint64_t board, SLuminophoreBoardPoint preferred, SLuminophoreNormalizedBox box) {
        if (!key || !box.valid())
            return false;
        if (next.boardFor(key)) {
            if (!apply(NS::SRemove{key, NS::eRemovalCause::RECLASSIFY}))
                return false;
        }
        auto occupied = next.occupancyState();
        if (!occupied || !occupied->boards.contains(board))
            return false;
        auto        point = NS::SPoint{preferred.x, preferred.y};
        const auto& cells = occupied->boards.at(board).tiled;
        const auto  free  = freeCoordinate(cells, point);
        if (!free)
            return false;
        point                  = *free;
        auto&      destination = s.state.boards.at(board);
        const auto oldView     = destination.view;
        if (!NS::includePoint(destination.view, point))
            return false;
        if (destination.view != oldView)
            destination.lastChange = NS::eViewOrigin::AUTO;
        s.floatingBoards[key]     = board;
        next.m_floatingHosts[key] = {point.x, point.y};
        next.m_floatingBoxes[key] = box;
        return true;
    };
    const bool valid = std::visit(
        [&](const auto& p) -> bool {
            using T = std::decay_t<decltype(p)>;
            if constexpr (std::is_same_v<T, SSpatialFocusCommand>) {
                if (p.outputID && s.bindings.contains(p.outputID))
                    s.selectedOutput = p.outputID;
                auto key = p.key;
                if (p.reveal) {
                    const auto board  = key ? next.boardFor(*key) : std::nullopt;
                    const auto point  = key ? next.coordinateOf(*key) : std::nullopt;
                    const auto output = key ? next.outputFor(*key) : std::nullopt;
                    if (!board || !point || !output)
                        return false;
                    auto       view = s.state.boards.at(*board).view;
                    const auto x    = NS::checkedAdd(point->x, 1 - view.columns);
                    const auto y    = NS::checkedAdd(point->y, 1 - view.rows);
                    if (!x || !y)
                        return false;
                    view.origin.x = std::clamp(view.origin.x, *x, int64_t(point->x));
                    view.origin.y = std::clamp(view.origin.y, *y, int64_t(point->y));
                    if (!(next.m_presentationMode == eLuminophorePresentationMode::WIDE && next.m_wideKey == key) && view != s.state.boards.at(*board).view &&
                        !apply(NS::SSetView{*board, view}))
                        return false;
                    if (next.m_presentationMode == eLuminophorePresentationMode::DESKTOP ||
                        (next.m_presentationMode == eLuminophorePresentationMode::WIDE && next.m_wideKey != key)) {
                        next.m_presentationMode = eLuminophorePresentationMode::NORMAL;
                        next.m_wideKey.reset();
                    }
                    s.selectedOutput = *output;
                    focusUpdate      = true;
                    nextFocus        = key;
                }
                if (key && !next.boardFor(*key))
                    key.reset();
                return apply(NS::SSetFocus{key});
            } else if constexpr (std::is_same_v<T, SSpatialTopologyCommand>) {
                if (p.revision < next.m_outputTopologyRevision || p.columns < 1 || p.rows < 1)
                    return false;
                std::set<std::string> connectors;
                auto                  oldOutputs = s.outputs;
                s.outputs.clear();
                s.bindings.clear();
                s.defaultColumns = p.columns;
                s.defaultRows    = p.rows;
                for (const auto& o : p.outputs) {
                    if (!o.id || o.name.empty() || o.width <= 0 || o.height <= 0 || s.outputs.contains(o.id) || !connectors.insert(o.name).second)
                        return false;
                    s.outputs[o.id] = o;
                    auto     known  = s.knownBoards.find(o.name);
                    uint64_t id     = 0;
                    if (known == s.knownBoards.end()) {
                        id                    = s.state.boards.empty() ? 1 : s.state.boards.rbegin()->first + 1;
                        s.knownBoards[o.name] = id;
                        s.state.boards[id]    = NS::SBoard{.view = {{0, 0}, p.columns, p.rows}, .defaultColumns = p.columns, .defaultRows = p.rows};
                    } else
                        id = known->second;
                    s.bindings[o.id]                     = id;
                    s.state.boards.at(id).defaultColumns = p.columns;
                    s.state.boards.at(id).defaultRows    = p.rows;
                }
                std::set<uint64_t> active;
                for (const auto& [out, id] : s.bindings)
                    active.insert(id);
                for (auto it = s.displaced.begin(); it != s.displaced.end();) {
                    if (!active.contains(it->second.board)) {
                        ++it;
                        continue;
                    }
                    const auto current = next.boardFor(it->first);
                    if (current && !s.floatingBoards.contains(it->first)) {
                        auto& origin = s.state.boards.at(it->second.board);
                        if (origin.tiled.contains(it->second.point)) {
                            ++it;
                            continue;
                        }
                        auto& from = s.state.boards.at(*current).tiled;
                        std::erase_if(from, [&](const auto& e) { return e.second == it->first; });
                        origin.tiled[it->second.point] = it->first;
                    } else if (current) {
                        s.floatingBoards[it->first]     = it->second.board;
                        next.m_floatingHosts[it->first] = {it->second.point.x, it->second.point.y};
                    }
                    it = s.displaced.erase(it);
                }
                if (!s.outputs.empty()) {
                    struct STransfer {
                        uint64_t   board, dest, key;
                        NS::SPoint point;
                        bool       floating;
                    };
                    std::vector<STransfer> transfers;
                    for (const auto& [id, b] : s.state.boards) {
                        if (active.contains(id))
                            continue;
                        SLuminophoreOutputGeometry previous;
                        for (const auto& [out, o] : oldOutputs) {
                            const auto k = s.knownBoards.find(o.name);
                            if (k != s.knownBoards.end() && k->second == id)
                                previous = o;
                        }
                        auto closest = s.outputs.begin();
                        for (auto it = s.outputs.begin(); it != s.outputs.end(); ++it)
                            if (distance(previous, it->second) < distance(previous, closest->second))
                                closest = it;
                        const auto dest = s.bindings.at(closest->first);
                        for (const auto& [point, key] : b.tiled)
                            transfers.push_back({id, dest, key, point, false});
                        for (const auto& [key, host] : s.floatingBoards)
                            if (host == id) {
                                const auto point = next.m_floatingHosts.at(key);
                                transfers.push_back({id, dest, key, {point.x, point.y}, true});
                            }
                    }
                    for (const auto& t : transfers) {
                        s.displaced.try_emplace(t.key, SLuminophoreIndependentState::SReturn{t.board, t.point});
                        if (t.floating) {
                            if (!addFloating(t.key, t.dest, next.m_floatingHosts.at(t.key), next.m_floatingBoxes.at(t.key)))
                                return false;
                            continue;
                        }
                        s.state.boards.at(t.board).tiled.erase(t.point);
                        const auto focus = s.state.focus;
                        s.state.focus.reset();
                        if (!apply(NS::SCreate{t.dest, t.key}))
                            return false;
                        s.state.focus = focus;
                    }
                    if (!s.outputs.contains(s.selectedOutput))
                        s.selectedOutput = s.outputs.begin()->first;
                }
                next.m_outputTopologyRevision = p.revision;
                return true;
            } else if constexpr (std::is_same_v<T, SObserveWindowCommand>) {
                const auto existing = next.boardFor(p.key);
                if (p.mode == eLuminophoreWindowPlacementMode::ABSENT) {
                    if (existing && !apply(NS::SRemove{p.key, p.closed ? NS::eRemovalCause::CLOSE : NS::eRemovalCause::RECLASSIFY}))
                        return false;
                    s.floatingBoards.erase(p.key);
                    next.eraseFloating(p.key);
                    s.displaced.erase(p.key);
                    return true;
                }
                const auto board = existing ? existing : boardAt(p.outputID);
                if (!board)
                    return false;
                if (p.mode == eLuminophoreWindowPlacementMode::TILED) {
                    if (existing && !s.floatingBoards.contains(p.key))
                        return true;
                    const auto formerHost = next.floatingHostOf(p.key);
                    next.eraseFloating(p.key);
                    s.floatingBoards.erase(p.key);
                    if (formerHost)
                        return s.state.boards.at(*board).tiled.emplace(NS::SPoint{formerHost->x, formerHost->y}, p.key).second;
                    auto placementBoard = *board;
                    auto preserved      = s.state.boards.at(placementBoard).view;
                    auto origin         = s.state.boards.at(placementBoard).lastChange;
                    bool created        = false;
                    if (!created && p.causalSource && !existing && !s.floatingBoards.contains(p.causalSource)) {
                        const auto sourceBoard = next.boardFor(p.causalSource);
                        const auto sourcePoint = next.coordinateOf(p.causalSource);
                        if (sourceBoard && sourcePoint && next.outputFor(p.causalSource)) {
                            const auto sourceView   = s.state.boards.at(*sourceBoard).view;
                            const auto sourceChange = s.state.boards.at(*sourceBoard).lastChange;
                            created                 = apply(NS::SCreate{*sourceBoard, p.key, NS::SPoint{sourcePoint->x, sourcePoint->y}});
                            if (created) {
                                placementBoard = *sourceBoard;
                                preserved      = sourceView;
                                origin         = sourceChange;
                            }
                        }
                    }
                    if (!created && !p.causalSource && p.directLaunch && !p.initialPlacement.empty() && p.initialPlacement != "default") {
                        if (!apply(NS::SCreate{*board, p.key, std::nullopt, p.initialPlacement}))
                            return false;
                        created = true;
                    }
                    if (!created && !apply(NS::SCreate{*board, p.key}))
                        return false;
                    if (next.m_wideKey) {
                        s.state.boards.at(placementBoard).view       = preserved;
                        s.state.boards.at(placementBoard).lastChange = origin;
                    }
                    return true;
                }
                const auto point = p.preferred.value_or(SLuminophoreBoardPoint{s.state.boards.at(*board).view.origin.x, s.state.boards.at(*board).view.origin.y});
                if (existing && s.floatingBoards.contains(p.key) && next.m_floatingHosts.at(p.key) == point) {
                    if (p.localBox)
                        next.m_floatingBoxes[p.key] = *p.localBox;
                    return next.m_floatingBoxes.at(p.key).valid();
                }
                return addFloating(p.key, *board, point, p.localBox.value_or(SLuminophoreNormalizedBox{}));
            } else if constexpr (std::is_same_v<T, SAddTiledCommand>) {
                const auto board = boardAt(0);
                return board && apply(NS::SCreate{*board, p.key});
            } else if constexpr (std::is_same_v<T, SRemoveWindowCommand>) {
                s.displaced.erase(p.key);
                return apply(NS::SRemove{p.key});
            } else if constexpr (std::is_same_v<T, SMoveWindowToCommand>) {
                if (p.expectedTopologyRevision != next.m_outputTopologyRevision)
                    return false;
                return moveTo(p.key, p.outputID, {p.point.x, p.point.y});
            } else if constexpr (std::is_same_v<T, SMoveTiledCommand>) {
                const auto pos = next.coordinateOf(p.key).or_else([&] { return next.floatingHostOf(p.key); });
                const auto out = next.outputFor(p.key);
                if (!pos || !out)
                    return false;
                auto x = NS::checkedAdd(pos->x, p.direction == eLuminophoreSpatialDirection::LEFT ? -1 : p.direction == eLuminophoreSpatialDirection::RIGHT ? 1 : 0);
                auto y = NS::checkedAdd(pos->y, p.direction == eLuminophoreSpatialDirection::UP ? -1 : p.direction == eLuminophoreSpatialDirection::DOWN ? 1 : 0);
                return x && y && moveTo(p.key, *out, {*x, *y});
            } else if constexpr (std::is_same_v<T, SMoveOutputViewToCommand> || std::is_same_v<T, SResizeOutputViewCommand> || std::is_same_v<T, SMoveViewCommand> ||
                                 std::is_same_v<T, SAdjustViewCommand>) {
                if (p.expectedTopologyRevision != next.m_outputTopologyRevision)
                    return false;
                const auto id = boardAt(p.outputID);
                if (!id)
                    return false;
                auto v = s.state.boards.at(*id).view;
                if constexpr (std::is_same_v<T, SMoveOutputViewToCommand>)
                    v.origin = {p.origin.x, p.origin.y};
                else if constexpr (std::is_same_v<T, SResizeOutputViewCommand>)
                    v = {{p.rect.origin.x, p.rect.origin.y}, p.rect.columns, p.rect.rows};
                else {
                    const int dx = p.direction == eLuminophoreSpatialDirection::LEFT ? -1 : p.direction == eLuminophoreSpatialDirection::RIGHT ? 1 : 0;
                    const int dy = p.direction == eLuminophoreSpatialDirection::UP ? -1 : p.direction == eLuminophoreSpatialDirection::DOWN ? 1 : 0;
                    if constexpr (std::is_same_v<T, SMoveViewCommand>) {
                        const auto x = NS::checkedAdd(v.origin.x, dx), y = NS::checkedAdd(v.origin.y, dy);
                        if (!x || !y)
                            return false;
                        v.origin = {*x, *y};
                    } else {
                        const NS::SPoint anchor{p.anchor.x, p.anchor.y};
                        if (!v.contains(anchor))
                            return false;
                        if (dx < 0) {
                            if (anchor.x < v.origin.x + v.columns - 1)
                                --v.columns;
                            else {
                                const auto x = NS::checkedAdd(v.origin.x, -1);
                                if (!x)
                                    return false;
                                v.origin.x = *x;
                                ++v.columns;
                            }
                        } else if (dx > 0) {
                            if (anchor.x > v.origin.x) {
                                ++v.origin.x;
                                --v.columns;
                            } else
                                ++v.columns;
                        } else if (dy < 0) {
                            if (anchor.y < v.origin.y + v.rows - 1)
                                --v.rows;
                            else {
                                const auto y = NS::checkedAdd(v.origin.y, -1);
                                if (!y)
                                    return false;
                                v.origin.y = *y;
                                ++v.rows;
                            }
                        } else if (dy > 0) {
                            if (anchor.y > v.origin.y) {
                                ++v.origin.y;
                                --v.rows;
                            } else
                                ++v.rows;
                        }
                    }
                }
                if (v.columns > INT_MAX || v.rows > INT_MAX)
                    return false;
                return apply(NS::SSetView{*id, v});
            } else if constexpr (std::is_same_v<T, SSpatialResizeCommand>) {
                const auto id = next.boardFor(p.key);
                if (!id || !s.meshes.contains(*id))
                    return false;
                auto& mesh = s.meshes.at(*id);
                auto  fill = NS::computeOwnership(s.state.boards.at(*id), mesh.fillFocus);
                if (p.side < 0 || p.side > 3 || p.minimum < 1)
                    return false;
                const NS::SResizeRequest first{mesh.revision, p.face, static_cast<NS::eSide>(p.side), p.delta, p.minimum};
                const auto               r = NS::solveWindowResize(mesh, fill, p.key, first, p.clientMinimums);
                if (r.status != NS::eResizeStatus::APPLIED && r.status != NS::eResizeStatus::NO_CHANGE)
                    return false;
                if (p.second) {
                    auto second             = *p.second;
                    second.expectedRevision = r.mesh.revision;
                    auto corner             = NS::solveWindowResize(r.mesh, fill, p.key, second, p.clientMinimums);
                    if (corner.status != NS::eResizeStatus::APPLIED && corner.status != NS::eResizeStatus::NO_CHANGE)
                        return false;
                    corner.mesh.revision = mesh.revision + ((r.effectiveDelta || corner.effectiveDelta) ? 1 : 0);
                    mesh                 = std::move(corner.mesh);
                } else
                    mesh = r.mesh;
                return true;
            } else if constexpr (std::is_same_v<T, SFocusDirectionCommand>) {
                const auto id     = next.boardFor(p.key);
                const auto origin = next.coordinateOf(p.key);
                if (!id || !origin)
                    return false;
                long double best = std::numeric_limits<long double>::max();
                const auto& b    = s.state.boards.at(*id);
                for (const auto& [point, key] : b.tiled) {
                    if (key == p.key || !b.view.contains(point))
                        continue;
                    const long double dx = static_cast<long double>(point.x) - origin->x, dy = static_cast<long double>(point.y) - origin->y;
                    if ((p.direction == eLuminophoreSpatialDirection::LEFT && dx >= 0) || (p.direction == eLuminophoreSpatialDirection::RIGHT && dx <= 0) ||
                        (p.direction == eLuminophoreSpatialDirection::UP && dy >= 0) || (p.direction == eLuminophoreSpatialDirection::DOWN && dy <= 0))
                        continue;
                    const auto score = dx * dx + dy * dy;
                    if (score < best) {
                        best      = score;
                        nextFocus = key;
                    }
                }
                if (nextFocus) {
                    focusUpdate = true;
                    return apply(NS::SSetFocus{nextFocus});
                }
                return true;
            } else if constexpr (std::is_same_v<T, SToggleDesktopCommand>) {
                if (next.m_presentationMode == eLuminophorePresentationMode::DESKTOP)
                    next.m_presentationMode = next.m_desktopReturnMode;
                else {
                    next.m_desktopReturnMode = next.m_presentationMode;
                    next.m_presentationMode  = eLuminophorePresentationMode::DESKTOP;
                }
                return true;
            } else if constexpr (std::is_same_v<T, SExitWideCommand>) {
                next.m_wideKey.reset();
                next.m_presentationMode = eLuminophorePresentationMode::NORMAL;
                return true;
            } else if constexpr (std::is_same_v<T, SToggleWideCommand> || std::is_same_v<T, SEnterWideCommand>) {
                if (!next.coordinateOf(p.key))
                    return false;
                if constexpr (std::is_same_v<T, SToggleWideCommand>)
                    if (next.m_wideKey == p.key && next.m_presentationMode == eLuminophorePresentationMode::WIDE) {
                        next.m_wideKey.reset();
                        next.m_presentationMode = eLuminophorePresentationMode::NORMAL;
                        return true;
                    }
                next.m_wideKey          = p.key;
                next.m_presentationMode = eLuminophorePresentationMode::WIDE;
                return true;
            } else if constexpr (std::is_same_v<T, SUpdateFloatingCommand> || std::is_same_v<T, SAttachFloatingCommand>) {
                if (!p.localBox.valid())
                    return false;
                auto id = p.outputID ? boardAt(p.outputID) : next.boardFor(p.key);
                if (!id)
                    id = boardAt(0);
                if (!id)
                    return false;
                if (s.floatingBoards.contains(p.key)) {
                    uint64_t output = p.outputID;
                    if (!output)
                        for (const auto& [candidate, board] : s.bindings)
                            if (board == *id)
                                output = candidate;
                    if (!moveTo(p.key, output, {p.host.x, p.host.y}))
                        return false;
                    next.m_floatingBoxes[p.key] = p.localBox;
                    return true;
                }
                return addFloating(p.key, *id, p.host, p.localBox);
            } else if constexpr (std::is_same_v<T, SDetachFloatingCommand>) {
                s.floatingBoards.erase(p.key);
                next.eraseFloating(p.key);
                return true;
            } else
                return false;
        },
        command.payload);
    if (valid && before.independent && (s.state.focus != before.independent->state.focus || s.selectedOutput != before.independent->selectedOutput))
        ++s.focusRevision;
    if (valid && std::holds_alternative<SSpatialTopologyCommand>(command.payload)) {
        const auto repaired = next.occupancyState(true);
        if (!repaired)
            return fail(eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
        next.assignOccupancy(*repaired);
    }
    if (s.state.focus && !next.boardFor(*s.state.focus))
        s.state.focus.reset();
    if (!valid || !next.occupancyState())
        return fail(eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    for (const auto& [id, board] : s.state.boards)
        if (board.view.columns > INT_MAX || board.view.rows > INT_MAX)
            return fail(eLuminophoreSpatialTransactionStatus::COMMIT_FAILED);
    for (const auto& [key, board] : s.floatingBoards)
        if (!key || !s.state.boards.contains(board) || !next.m_floatingHosts.contains(key) || !next.m_floatingBoxes.contains(key) || !next.m_floatingBoxes.at(key).valid())
            return fail(eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    next.syncIndependentCaches();
    if (next.m_wideKey && !next.coordinateOf(*next.m_wideKey)) {
        next.m_wideKey.reset();
        next.m_desktopReturnMode = eLuminophorePresentationMode::NORMAL;
        if (next.m_presentationMode == eLuminophorePresentationMode::WIDE)
            next.m_presentationMode = eLuminophorePresentationMode::NORMAL;
    }
    for (const auto& [output, id] : s.bindings) {
        const auto& o         = s.outputs.at(output);
        const auto& board     = s.state.boards.at(id);
        const auto  it        = s.meshes.find(id);
        const auto  fillFocus = std::holds_alternative<SSpatialResizeCommand>(command.payload) && it != s.meshes.end() ? it->second.fillFocus : s.state.focus;
        auto        mesh =
            it == s.meshes.end() ? NS::buildMesh(board, o.width, o.height, 1'000'000, fillFocus) : NS::reconcileMesh(it->second, board, o.width, o.height, 1'000'000, fillFocus);
        if (mesh.status != NS::eMeshStatus::OK)
            return fail(eLuminophoreSpatialTransactionStatus::COMMIT_FAILED);
        if (const auto focus = std::get_if<SSpatialFocusCommand>(&command.payload); focus && !focus->reveal && it != s.meshes.end()) {
            const auto oldRegions = NS::regionsForOwnership(it->second, NS::computeOwnership(board, it->second.fillFocus));
            const auto newRegions = NS::regionsForOwnership(mesh.mesh, NS::computeOwnership(board, mesh.mesh.fillFocus));
            if (!oldRegions || !newRegions)
                return fail(eLuminophoreSpatialTransactionStatus::COMMIT_FAILED);
            for (const auto& old : *oldRegions) {
                const auto current = std::ranges::find(*newRegions, old.owner, &NS::SRegion::owner);
                if (current == newRegions->end())
                    continue;
                NS::SPoint minimum{100, 100};
                if (const auto hint = focus->clientMinimums.find(old.owner); hint != focus->clientMinimums.end()) {
                    if (hint->second.x < 1 || hint->second.y < 1)
                        return fail(eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
                    minimum.x = std::max(minimum.x, hint->second.x);
                    minimum.y = std::max(minimum.y, hint->second.y);
                }
                const auto& a = old.boxes.front();
                const auto& b = current->boxes.front();
                if (b.width < std::min(a.width, minimum.x) || b.height < std::min(a.height, minimum.y)) {
                    mesh = {NS::eMeshStatus::OK, it->second, false};
                    break;
                }
            }
        }
        // Same-view no-op reconciliations must not manufacture a model change.
        if (it != s.meshes.end() && mesh.mesh.faces == it->second.faces && mesh.mesh.width == it->second.width && mesh.mesh.height == it->second.height)
            mesh.mesh.revision = it->second.revision;
        if (mesh.reset)
            s.meshResetRevisions[id] = m_revision + 1;
        s.meshes[id] = std::move(mesh.mesh);
    }
    // A logical exchange must not move an unrelated floating window's pixels.
    // Re-express its prior box against the new host without changing rendering.
    for (const auto& [key, board] : s.floatingBoards) {
        const auto old         = std::ranges::find(before.floating, key, &SLuminophoreFloatingPlacement::key);
        const auto oldBoard    = before.independent->floatingBoards.find(key);
        const auto host        = next.m_floatingHosts.at(key);
        const auto moved       = std::get_if<SMoveWindowToCommand>(&command.payload);
        const auto directional = std::get_if<SMoveTiledCommand>(&command.payload);
        if ((moved && moved->key == key) || (directional && directional->key == key))
            continue; // Keep the established physical movement of the grabbed window.
        const auto update      = std::get_if<SUpdateFloatingCommand>(&command.payload);
        const auto observe     = std::get_if<SObserveWindowCommand>(&command.payload);
        const bool explicitBox = (update && update->key == key) || (observe && observe->key == key && observe->localBox);
        if (!explicitBox && (old == before.floating.end() || oldBoard == before.independent->floatingBoards.end() || (old->host == host && oldBoard->second == board)))
            continue;
        std::optional<SLuminophorePhysicalBox> physical;
        std::optional<SLuminophorePhysicalBox> sourceOutput;
        for (const auto& [output, id] : before.independent->bindings) {
            const auto&                      o = before.independent->outputs.at(output);
            const SLuminophorePhysicalOutput extent{output, o.name, {o.x, o.y, o.width, o.height}};
            const auto                       sourcePoint = explicitBox ? update ? update->host : observe->preferred.value_or(host) : old->host;
            const auto                       sourceBoard = explicitBox ? board : oldBoard->second;
            if (id != sourceBoard)
                continue;
            sourceOutput = extent.box;
            if (const auto reference = CLuminophoreSpatialProjection::hostBox(before, sourcePoint, extent))
                physical = CLuminophoreSpatialProjection::denormalize(explicitBox ? update ? update->localBox : *observe->localBox : old->localBox, *reference);
        }
        if (!physical)
            continue;
        for (const auto& [output, id] : s.bindings) {
            if (id != board)
                continue;
            const auto&                      o = s.outputs.at(output);
            const SLuminophorePhysicalOutput extent{output, o.name, {o.x, o.y, o.width, o.height}};
            if (const auto reference = CLuminophoreSpatialProjection::hostBox(next.snapshot(), host, extent)) {
                auto desired = *physical;
                if (!explicitBox && sourceOutput && oldBoard->second != board) {
                    const int64_t x = int64_t(desired.x) + extent.box.x - sourceOutput->x;
                    const int64_t y = int64_t(desired.y) + extent.box.y - sourceOutput->y;
                    if (x < INT_MIN || x > INT_MAX || y < INT_MIN || y > INT_MAX)
                        return fail(eLuminophoreSpatialTransactionStatus::COMMIT_FAILED);
                    desired.x = static_cast<int>(x);
                    desired.y = static_cast<int>(y);
                }
                const auto local = CLuminophoreSpatialProjection::normalize(desired, *reference);
                if (!local)
                    return fail(eLuminophoreSpatialTransactionStatus::COMMIT_FAILED);
                next.m_floatingBoxes[key] = *local;
            }
        }
    }
    if (next.snapshot() == before)
        return fail(eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    next.m_revision = m_revision + 1;
    *this           = std::move(next);
    return {.status = eLuminophoreSpatialTransactionStatus::APPLIED, .moveResult = moveResult, .updatesFocus = focusUpdate, .nextFocusedKey = nextFocus, .snapshot = snapshot()};
}

bool CLuminophoreSpatialModel::isWideKey(LuminophoreWindowKey key) const {
    return m_presentationMode == eLuminophorePresentationMode::WIDE && m_wideKey == key;
}
