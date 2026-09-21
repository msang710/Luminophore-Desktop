#include "LuminophoreSpatialModel.hpp"
#include "LuminophoreSpatialPush.hpp"
#include "LuminophoreOutputViewSolver.hpp"
#include "LuminophoreSpatialTransaction.hpp"

#include <algorithm>
#include <cmath>
#include <numeric>
#include <limits>
#include <set>
#include <tuple>

bool SLuminophoreNormalizedBox::valid() const {
    return logicalWidth >= 0 && logicalHeight >= 0 && ((logicalWidth == 0) == (logicalHeight == 0)) && width > 0 && height > 0 &&
        static_cast<int64_t>(x) + width <= std::numeric_limits<int>::max() && static_cast<int64_t>(y) + height <= std::numeric_limits<int>::max();
}
#include <type_traits>

eLuminophoreSpatialDirection luminophoreSpatialDirectionForDelta(int64_t dx, int64_t dy) {
    if (std::abs(dx) >= std::abs(dy))
        return dx < 0 ? eLuminophoreSpatialDirection::LEFT : eLuminophoreSpatialDirection::RIGHT;
    return dy < 0 ? eLuminophoreSpatialDirection::UP : eLuminophoreSpatialDirection::DOWN;
}

SLuminophoreBoardExtent SLuminophoreBoardExtent::fromPhysicalExtent(int width, int height) {
    if (width <= 0 || height <= 0)
        return {};

    const int rows    = std::min(5, std::max(1, height / std::gcd(width, height)));
    const int columns = std::clamp<int64_t>(static_cast<int>(std::round(static_cast<double>(rows) * width / height)), 1, 15);
    return {.columns = columns, .rows = rows};
}

bool SLuminophoreBoardExtent::contains(const SLuminophoreBoardPoint& point) const {
    return point.x >= 0 && point.y >= 0 && point.x < columns && point.y < rows;
}

bool SLuminophoreViewRect::contains(const SLuminophoreBoardPoint& point) const {
    return point.x >= origin.x && point.y >= origin.y && static_cast<__int128_t>(point.x) < static_cast<__int128_t>(origin.x) + columns &&
        static_cast<__int128_t>(point.y) < static_cast<__int128_t>(origin.y) + rows;
}

CLuminophoreSpatialModel::CLuminophoreSpatialModel() : CLuminophoreSpatialModel(SLuminophoreBoardExtent{}) {
    m_independent.emplace();
}
CLuminophoreSpatialModel CLuminophoreSpatialModel::finiteFixture(SLuminophoreBoardExtent extent) {
    return CLuminophoreSpatialModel(extent);
}

CLuminophoreSpatialModel::CLuminophoreSpatialModel(SLuminophoreBoardExtent extent) : m_extent({std::max(1, extent.columns), std::max(1, extent.rows)}) {
    resetDefaultViewImpl({});
    m_revision = 0;
}

const SLuminophoreBoardExtent& CLuminophoreSpatialModel::extent() const {
    return m_extent;
}

const SLuminophoreViewRect& CLuminophoreSpatialModel::view() const {
    return m_view;
}

const std::vector<SLuminophoreOutputView>& CLuminophoreSpatialModel::outputViews() const {
    return m_outputViews;
}

uint64_t CLuminophoreSpatialModel::outputTopologyRevision() const {
    return m_outputTopologyRevision;
}

uint64_t CLuminophoreSpatialModel::revision() const {
    return m_revision;
}

SLuminophoreSpatialSnapshot CLuminophoreSpatialModel::snapshot() const {
    SLuminophoreSpatialSnapshot result = {
        .extent                 = m_extent,
        .view                   = m_view,
        .presentationMode       = m_presentationMode,
        .outputViews            = m_outputViews,
        .wideKey                = m_wideKey,
        .outputTopologyRevision = m_outputTopologyRevision,
        .revision               = m_revision,
        .desktopReturnMode      = m_desktopReturnMode,
    };
    if (m_independent) {
        result.independent = m_independent;
        for (const auto& [id, board] : m_independent->state.boards)
            for (const auto& [point, key] : board.tiled)
                result.tiled.push_back({.key = key, .point = {point.x, point.y}});
    }
    result.tiled.reserve(result.tiled.size() + m_tiled.size());
    for (const auto& [point, key] : m_tiled)
        if (!m_independent)
            result.tiled.emplace_back(SLuminophoreTiledPlacement{.key = key, .point = point});
    result.floating.reserve(m_floatingHosts.size());
    for (const auto& [key, host] : m_floatingHosts)
        result.floating.emplace_back(SLuminophoreFloatingPlacement{.key = key, .host = host, .localBox = m_floatingBoxes.at(key)});
    return result;
}

SLuminophoreSpatialTransactionResult CLuminophoreSpatialModel::preview(const SLuminophoreSpatialCommand& command) const {
    auto candidate = *this;
    return candidate.transact(command);
}

SLuminophoreSpatialTransactionResult CLuminophoreSpatialModel::transact(const SLuminophoreSpatialCommand& command) {
    if (m_independent)
        return transactIndependent(command);
    if (command.expectedRevision != m_revision)
        return {.status = eLuminophoreSpatialTransactionStatus::STALE_REVISION, .snapshot = snapshot()};

    CLuminophoreSpatialModel            next         = *this;
    auto                         status       = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND;
    auto                         moveResult   = eLuminophoreSpatialMoveResult::REJECTED;
    bool                         updatesFocus = false;
    std::optional<LuminophoreWindowKey> nextFocusedKey;

    std::visit(
        [&](const auto& payload) {
            using T = std::decay_t<decltype(payload)>;
            if constexpr (std::is_same_v<T, SAddTiledCommand>) {
                if (payload.key == 0 || next.m_tiledCoordinates.contains(payload.key) || next.m_floatingHosts.contains(payload.key))
                    return;
                if (!payload.preferred && !next.firstVacant()) {
                    status = eLuminophoreSpatialTransactionStatus::NO_CAPACITY;
                    return;
                }
                status = next.addTiledImpl(payload.key, payload.preferred) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CAPACITY;
            } else if constexpr (std::is_same_v<T, SRemoveWindowCommand>) {
                status = next.removeImpl(payload.key) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SMoveTiledCommand>) {
                moveResult = next.moveTiledImpl(payload.key, payload.direction);
                status     = moveResult == eLuminophoreSpatialMoveResult::REJECTED ? eLuminophoreSpatialTransactionStatus::NO_CHANGE : eLuminophoreSpatialTransactionStatus::APPLIED;
                // Moving a coordinate never changes the seat's focused window.
            } else if constexpr (std::is_same_v<T, SMoveWindowToCommand>) {
                if (payload.expectedTopologyRevision != next.m_outputTopologyRevision) {
                    status = eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY;
                    return;
                }
                if (!next.m_extent.contains(payload.point) || std::ranges::none_of(next.m_outputViews, [&](const auto& view) { return view.outputID == payload.outputID; }))
                    return;
                if (const auto source = next.coordinateOf(payload.key)) {
                    if (*source == payload.point) {
                        status = eLuminophoreSpatialTransactionStatus::NO_CHANGE;
                        return;
                    }
                    const auto direction = luminophoreSpatialDirectionForDelta(static_cast<int64_t>(payload.point.x) - source->x, static_cast<int64_t>(payload.point.y) - source->y);
                    moveResult           = next.moveTiledToImpl(payload.key, payload.point, direction);
                    status               = moveResult == eLuminophoreSpatialMoveResult::REJECTED ? eLuminophoreSpatialTransactionStatus::NO_CHANGE : eLuminophoreSpatialTransactionStatus::APPLIED;
                } else if (const auto host = next.floatingHostOf(payload.key)) {
                    status = *host == payload.point                                                               ? eLuminophoreSpatialTransactionStatus::NO_CHANGE :
                        next.updateFloatingImpl(payload.key, payload.point, next.m_floatingBoxes.at(payload.key)) ? eLuminophoreSpatialTransactionStatus::APPLIED :
                                                                                                                    eLuminophoreSpatialTransactionStatus::INVALID_COMMAND;
                }
            } else if constexpr (std::is_same_v<T, SMoveOutputViewToCommand> || std::is_same_v<T, SResizeOutputViewCommand>) {
                if (payload.expectedTopologyRevision != next.m_outputTopologyRevision) {
                    status = eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY;
                    return;
                }
                const auto output = std::ranges::find(next.m_outputViews, payload.outputID, &SLuminophoreOutputView::outputID);
                if (output == next.m_outputViews.end())
                    return;
                auto rect = output->rect;
                if constexpr (std::is_same_v<T, SMoveOutputViewToCommand>)
                    rect.origin = payload.origin;
                else
                    rect = payload.rect;
                const auto solved = CLuminophoreOutputViewSolver::place(next.m_extent, next.m_outputViews, payload.outputID, rect);
                if (!solved)
                    return;
                status = eLuminophoreSpatialTransactionStatus::NO_CHANGE;
                if (*solved == next.m_outputViews || next.m_presentationMode == eLuminophorePresentationMode::WIDE)
                    return;
                next.m_outputViews = *solved;
                ++next.m_revision;
                status = eLuminophoreSpatialTransactionStatus::APPLIED;
            } else if constexpr (std::is_same_v<T, SAttachFloatingCommand>) {
                status =
                    next.attachFloatingImpl(payload.key, payload.host, payload.localBox) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::INVALID_COMMAND;
            } else if constexpr (std::is_same_v<T, SUpdateFloatingCommand>) {
                if (next.floatingHostOf(payload.key) == payload.host && next.m_floatingBoxes.contains(payload.key) && next.m_floatingBoxes.at(payload.key) == payload.localBox) {
                    status = eLuminophoreSpatialTransactionStatus::NO_CHANGE;
                    return;
                }
                status =
                    next.updateFloatingImpl(payload.key, payload.host, payload.localBox) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::INVALID_COMMAND;
            } else if constexpr (std::is_same_v<T, SDetachFloatingCommand>) {
                status = next.detachFloatingImpl(payload.key) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SFocusDirectionCommand>) {
                if (payload.expectedTopologyRevision != next.m_outputTopologyRevision) {
                    status = eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY;
                    return;
                }
                const auto origin = next.coordinateOf(payload.key);
                if (!origin || std::ranges::none_of(next.m_outputViews, [&](const auto& view) { return view.outputID == payload.outputID; }))
                    return;
                std::optional<SLuminophoreTiledPlacement>                                   target;
                std::optional<std::tuple<int64_t, int64_t, int, int, LuminophoreWindowKey>> best;
                for (const auto& [point, key] : next.m_tiled) {
                    const int64_t dx            = static_cast<int64_t>(point.x) - origin->x;
                    const int64_t dy            = static_cast<int64_t>(point.y) - origin->y;
                    bool          eligible      = false;
                    int64_t       perpendicular = 0;
                    switch (payload.direction) {
                        case eLuminophoreSpatialDirection::LEFT:
                            eligible      = dx < 0;
                            perpendicular = std::abs(dy);
                            break;
                        case eLuminophoreSpatialDirection::RIGHT:
                            eligible      = dx > 0;
                            perpendicular = std::abs(dy);
                            break;
                        case eLuminophoreSpatialDirection::UP:
                            eligible      = dy < 0;
                            perpendicular = std::abs(dx);
                            break;
                        case eLuminophoreSpatialDirection::DOWN:
                            eligible      = dy > 0;
                            perpendicular = std::abs(dx);
                            break;
                    }
                    if (!eligible)
                        continue;
                    const auto rank = std::tuple{dx * dx + dy * dy, perpendicular, point.y, point.x, key};
                    if (!best || rank < *best) {
                        best   = rank;
                        target = SLuminophoreTiledPlacement{.key = key, .point = point};
                    }
                }
                status = eLuminophoreSpatialTransactionStatus::NO_CHANGE;
                if (!target)
                    return;
                if (std::ranges::none_of(next.m_outputViews, [&](const auto& view) { return view.rect.contains(target->point); })) {
                    const auto views = CLuminophoreOutputViewSolver::reveal(next.m_extent, next.m_outputViews, payload.outputID, target->point, payload.direction);
                    if (!views)
                        return;
                    next.m_outputViews = *views;
                }
                // Explicit focus reveals the target in NORMAL before changing the seat.
                next.m_presentationMode = eLuminophorePresentationMode::NORMAL;
                next.m_wideKey.reset();
                next.m_desktopReturnMode = eLuminophorePresentationMode::NORMAL;
                ++next.m_revision;
                updatesFocus   = true;
                nextFocusedKey = target->key;
                status         = eLuminophoreSpatialTransactionStatus::APPLIED;
            } else if constexpr (std::is_same_v<T, SMoveViewCommand>) {
                if (next.m_presentationMode == eLuminophorePresentationMode::WIDE) {
                    status = eLuminophoreSpatialTransactionStatus::NO_CHANGE;
                    return;
                }
                if (payload.outputID != 0 && payload.expectedTopologyRevision != next.m_outputTopologyRevision) {
                    status = eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY;
                    return;
                }
                status = (payload.outputID == 0 ? next.moveViewImpl(payload.direction) :
                                                  next.moveOutputViewImpl(payload.outputID, payload.expectedTopologyRevision, payload.direction)) ?
                    eLuminophoreSpatialTransactionStatus::APPLIED :
                    eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SAdjustViewCommand>) {
                if (next.m_presentationMode == eLuminophorePresentationMode::WIDE) {
                    status = eLuminophoreSpatialTransactionStatus::NO_CHANGE;
                    return;
                }
                if (payload.outputID != 0 && payload.expectedTopologyRevision != next.m_outputTopologyRevision) {
                    status = eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY;
                    return;
                }
                status = (payload.outputID == 0 ? next.adjustViewImpl(payload.direction, payload.anchor) :
                                                  next.adjustOutputViewImpl(payload.outputID, payload.expectedTopologyRevision, payload.direction, payload.anchor)) ?
                    eLuminophoreSpatialTransactionStatus::APPLIED :
                    eLuminophoreSpatialTransactionStatus::NO_CHANGE;
                if (status == eLuminophoreSpatialTransactionStatus::APPLIED && payload.outputID != 0)
                    next.setOutputAnchor(payload.outputID, payload.anchorKey);
            } else if constexpr (std::is_same_v<T, SConfigureOutputViewsCommand>) {
                status = next.configureOutputViewsImpl(payload.views, payload.topologyRevision) ? eLuminophoreSpatialTransactionStatus::APPLIED :
                                                                                                  eLuminophoreSpatialTransactionStatus::INVALID_COMMAND;
            } else if constexpr (std::is_same_v<T, SConfigureTopologyCommand>) {
                status = next.configureTopologyImpl(payload.extent, payload.outputIDs, payload.topologyRevision) ? eLuminophoreSpatialTransactionStatus::APPLIED :
                                                                                                                   eLuminophoreSpatialTransactionStatus::INVALID_COMMAND;
            } else if constexpr (std::is_same_v<T, SEnterWideCommand>) {
                status = next.enterWideImpl(payload.key) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SExitWideCommand>) {
                status = next.exitWideImpl() ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SToggleDesktopCommand>) {
                status = next.toggleDesktopImpl() ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SToggleWideCommand>) {
                status = (next.m_presentationMode == eLuminophorePresentationMode::WIDE ? next.exitWideImpl() : next.enterWideImpl(payload.key)) ?
                    eLuminophoreSpatialTransactionStatus::APPLIED :
                    eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SResetViewCommand>) {
                status = next.resetDefaultViewImpl(payload.anchor) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SReconfigureExtentCommand>) {
                status = next.reconfigureExtentImpl(payload.extent) ? eLuminophoreSpatialTransactionStatus::APPLIED : eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            } else if constexpr (std::is_same_v<T, SObserveWindowCommand>) {
                if (payload.key == 0 || (payload.mode == eLuminophoreWindowPlacementMode::FLOATING && (!payload.preferred || !next.m_extent.contains(*payload.preferred))))
                    return;
                if (payload.mode == eLuminophoreWindowPlacementMode::TILED && !next.m_tiledCoordinates.contains(payload.key) &&
                    next.m_tiled.size() >= static_cast<size_t>(next.m_extent.columns * next.m_extent.rows)) {
                    status = eLuminophoreSpatialTransactionStatus::NO_CAPACITY;
                    return;
                }
                if (payload.localBox && !payload.localBox->valid())
                    return;
                status = next.observeWindowImpl(payload.key, payload.mode, payload.preferred, payload.localBox) ? eLuminophoreSpatialTransactionStatus::APPLIED :
                                                                                                                  eLuminophoreSpatialTransactionStatus::NO_CHANGE;
            }
        },
        command.payload);

    if (status == eLuminophoreSpatialTransactionStatus::APPLIED) {
        next.normalizeWideState();
        next.normalizeOutputAnchors();
        if (next.m_revision != m_revision + 1 || !next.validate())
            return {.status = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND, .snapshot = snapshot()};
        *this = std::move(next);
    }
    return {.status = status, .moveResult = moveResult, .updatesFocus = updatesFocus, .nextFocusedKey = nextFocusedKey, .snapshot = snapshot()};
}

bool CLuminophoreSpatialModel::validate() const {
    if (m_independent)
        return occupancyState().has_value();
    if (m_extent.columns <= 0 || m_extent.rows <= 0 || m_view.columns <= 0 || m_view.rows <= 0 || !m_extent.contains(m_view.origin) ||
        m_view.origin.x + m_view.columns > m_extent.columns || m_view.origin.y + m_view.rows > m_extent.rows || m_tiled.size() != m_tiledCoordinates.size())
        return false;

    std::set<LuminophoreWindowKey> keys;
    for (const auto& [point, key] : m_tiled) {
        const auto reverse = m_tiledCoordinates.find(key);
        if (!m_extent.contains(point) || key == 0 || !keys.insert(key).second || reverse == m_tiledCoordinates.end() || reverse->second != point)
            return false;
    }

    size_t floatingCount = 0;
    for (const auto& [key, host] : m_floatingHosts) {
        const auto byPoint = m_floatingByPoint.find(host);
        const auto box     = m_floatingBoxes.find(key);
        if (key == 0 || !m_extent.contains(host) || m_tiledCoordinates.contains(key) || byPoint == m_floatingByPoint.end() || std::ranges::count(byPoint->second, key) != 1 ||
            box == m_floatingBoxes.end() || !box->second.valid())
            return false;
        ++floatingCount;
    }
    size_t reverseFloatingCount = 0;
    for (const auto& [point, windows] : m_floatingByPoint) {
        if (!m_extent.contains(point) || windows.empty())
            return false;
        for (const auto key : windows) {
            const auto host = m_floatingHosts.find(key);
            if (host == m_floatingHosts.end() || host->second != point)
                return false;
            ++reverseFloatingCount;
        }
    }
    if (m_desktopReturnMode == eLuminophorePresentationMode::DESKTOP || (m_presentationMode != eLuminophorePresentationMode::DESKTOP && m_desktopReturnMode != eLuminophorePresentationMode::NORMAL))
        return false;
    const auto effectiveMode = m_presentationMode == eLuminophorePresentationMode::DESKTOP ? m_desktopReturnMode : m_presentationMode;
    if (effectiveMode == eLuminophorePresentationMode::NORMAL && m_wideKey)
        return false;
    if (effectiveMode == eLuminophorePresentationMode::WIDE && (!m_wideKey || !m_tiledCoordinates.contains(*m_wideKey)))
        return false;
    if (std::ranges::any_of(m_outputViews, [&](const auto& view) { return view.anchorKey && !visibleOnOutput(*view.anchorKey, view.outputID); }))
        return false;
    return floatingCount == reverseFloatingCount && floatingCount == m_floatingBoxes.size() && CLuminophoreOutputViewSolver::validate(m_extent, m_outputViews);
}

bool CLuminophoreSpatialModel::addTiled(LuminophoreWindowKey key, std::optional<SLuminophoreBoardPoint> preferred) {
    return transact({.expectedRevision = m_revision, .payload = SAddTiledCommand{.key = key, .preferred = preferred}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::addTiledImpl(LuminophoreWindowKey key, std::optional<SLuminophoreBoardPoint> preferred) {
    if (key == 0 || m_tiledCoordinates.contains(key) || m_floatingHosts.contains(key))
        return false;

    const auto target = preferred && m_extent.contains(*preferred) && !m_tiled.contains(*preferred) ? preferred : firstVacant();
    if (!target)
        return false;

    auto next     = m_tiled;
    next[*target] = key;
    commitTiled(std::move(next));
    return true;
}

bool CLuminophoreSpatialModel::remove(LuminophoreWindowKey key) {
    return transact({.expectedRevision = m_revision, .payload = SRemoveWindowCommand{.key = key}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::removeImpl(LuminophoreWindowKey key) {
    if (const auto tiled = m_tiledCoordinates.find(key); tiled != m_tiledCoordinates.end()) {
        auto next = m_tiled;
        next.erase(tiled->second);
        commitTiled(std::move(next));
        return true;
    }

    return detachFloatingImpl(key);
}

std::optional<SLuminophoreBoardPoint> CLuminophoreSpatialModel::coordinateOf(LuminophoreWindowKey key) const {
    const auto found = m_tiledCoordinates.find(key);
    if (found == m_tiledCoordinates.end())
        return std::nullopt;
    return found->second;
}

std::optional<LuminophoreWindowKey> CLuminophoreSpatialModel::tiledAt(const SLuminophoreBoardPoint& point) const {
    if (m_independent) {
        const auto binding = m_independent->bindings.find(m_independent->selectedOutput);
        if (binding == m_independent->bindings.end())
            return std::nullopt;
        const auto& tiled = m_independent->state.boards.at(binding->second).tiled;
        const auto  it    = tiled.find({point.x, point.y});
        return it == tiled.end() ? std::nullopt : std::optional{it->second};
    }
    const auto found = m_tiled.find(point);
    if (found == m_tiled.end())
        return std::nullopt;
    return found->second;
}

std::vector<LuminophoreWindowKey> CLuminophoreSpatialModel::tiledWindows() const {
    std::vector<LuminophoreWindowKey> result;
    if (m_independent) {
        for (const auto& [key, point] : m_tiledCoordinates)
            result.push_back(key);
        return result;
    }
    result.reserve(m_tiled.size());
    for (const auto& [point, key] : m_tiled)
        result.emplace_back(key);
    return result;
}

std::vector<LuminophoreWindowKey> CLuminophoreSpatialModel::visibleTiled() const {
    std::vector<LuminophoreWindowKey> result;
    if (m_independent) {
        for (const auto& [key, point] : m_tiledCoordinates)
            if (visibleInNormal(key))
                result.push_back(key);
        return result;
    }
    for (const auto& [point, key] : m_tiled) {
        if (visibleInNormal(key))
            result.emplace_back(key);
    }
    return result;
}

eLuminophoreSpatialMoveResult CLuminophoreSpatialModel::moveTiled(LuminophoreWindowKey key, eLuminophoreSpatialDirection direction) {
    return transact({.expectedRevision = m_revision, .payload = SMoveTiledCommand{.key = key, .direction = direction}}).moveResult;
}

eLuminophoreSpatialMoveResult CLuminophoreSpatialModel::moveTiledImpl(LuminophoreWindowKey key, eLuminophoreSpatialDirection direction) {
    const auto source = coordinateOf(key);
    return source ? moveTiledToImpl(key, adjacent(*source, direction), direction) : eLuminophoreSpatialMoveResult::REJECTED;
}

eLuminophoreSpatialMoveResult CLuminophoreSpatialModel::moveTiledToImpl(LuminophoreWindowKey key, const SLuminophoreBoardPoint& target, eLuminophoreSpatialDirection direction) {
    const auto sourceIt = m_tiledCoordinates.find(key);
    if (sourceIt == m_tiledCoordinates.end() || !m_extent.contains(target) || sourceIt->second == target)
        return eLuminophoreSpatialMoveResult::REJECTED;
    const auto source = sourceIt->second;

    auto       next = m_tiled;
    if (!next.contains(target)) {
        next.erase(source);
        next[target] = key;
        commitTiled(std::move(next));
        return eLuminophoreSpatialMoveResult::MOVED;
    }

    auto pushed = next;
    if (pushChain(pushed, target, direction)) {
        pushed.erase(source);
        pushed[target] = key;
        commitTiled(std::move(pushed));
        return eLuminophoreSpatialMoveResult::PUSHED;
    }

    const auto relocation = relocationFor(target, source);
    if (relocation) {
        const auto displaced = next.at(target);
        next.erase(source);
        next.erase(target);
        next[*relocation] = displaced;
        next[target]      = key;
        commitTiled(std::move(next));
        return eLuminophoreSpatialMoveResult::RELOCATED;
    }

    std::swap(next[source], next[target]);
    commitTiled(std::move(next));
    return eLuminophoreSpatialMoveResult::SWAPPED;
}

bool CLuminophoreSpatialModel::attachFloating(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox) {
    return transact({.expectedRevision = m_revision, .payload = SAttachFloatingCommand{.key = key, .host = host, .localBox = localBox}}).status ==
        eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::attachFloatingImpl(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox) {
    if (key == 0 || !m_extent.contains(host) || !localBox.valid() || m_tiledCoordinates.contains(key) || m_floatingHosts.contains(key))
        return false;

    m_floatingHosts[key] = host;
    m_floatingBoxes[key] = localBox;
    m_floatingByPoint[host].emplace_back(key);
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::updateFloating(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox) {
    return transact({.expectedRevision = m_revision, .payload = SUpdateFloatingCommand{.key = key, .host = host, .localBox = localBox}}).status ==
        eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::updateFloatingImpl(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox) {
    const auto found = m_floatingHosts.find(key);
    if (found == m_floatingHosts.end() || !m_extent.contains(host) || !localBox.valid())
        return false;
    if (found->second == host && m_floatingBoxes.at(key) == localBox)
        return false;

    auto& previous = m_floatingByPoint[found->second];
    std::erase(previous, key);
    if (previous.empty())
        m_floatingByPoint.erase(found->second);
    found->second        = host;
    m_floatingBoxes[key] = localBox;
    m_floatingByPoint[host].emplace_back(key);
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::detachFloating(LuminophoreWindowKey key) {
    return transact({.expectedRevision = m_revision, .payload = SDetachFloatingCommand{.key = key}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::detachFloatingImpl(LuminophoreWindowKey key) {
    const auto found = m_floatingHosts.find(key);
    if (found == m_floatingHosts.end())
        return false;

    auto& windows = m_floatingByPoint[found->second];
    std::erase(windows, key);
    if (windows.empty())
        m_floatingByPoint.erase(found->second);
    m_floatingHosts.erase(found);
    m_floatingBoxes.erase(key);
    ++m_revision;
    return true;
}

std::optional<SLuminophoreBoardPoint> CLuminophoreSpatialModel::floatingHostOf(LuminophoreWindowKey key) const {
    const auto found = m_floatingHosts.find(key);
    if (found == m_floatingHosts.end())
        return std::nullopt;
    return found->second;
}

std::vector<LuminophoreWindowKey> CLuminophoreSpatialModel::floatingAt(const SLuminophoreBoardPoint& host) const {
    const auto found = m_floatingByPoint.find(host);
    if (found == m_floatingByPoint.end())
        return {};
    return found->second;
}

bool CLuminophoreSpatialModel::moveView(eLuminophoreSpatialDirection direction, uint64_t outputID, uint64_t expectedTopologyRevision) {
    return transact({
                        .expectedRevision = m_revision,
                        .payload          = SMoveViewCommand{.direction = direction, .outputID = outputID, .expectedTopologyRevision = expectedTopologyRevision},
                    })
               .status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::moveViewImpl(eLuminophoreSpatialDirection direction) {
    const auto previous = m_view;
    m_view.origin       = adjacent(m_view.origin, direction);
    clampView();
    if (m_view == previous)
        return false;
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::adjustView(eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor, uint64_t outputID, uint64_t expectedTopologyRevision) {
    return transact({
                        .expectedRevision = m_revision,
                        .payload = SAdjustViewCommand{.direction = direction, .anchor = anchor, .outputID = outputID, .expectedTopologyRevision = expectedTopologyRevision},
                    })
               .status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::configureOutputViews(std::vector<SLuminophoreOutputView> views, uint64_t topologyRevision) {
    return transact({
                        .expectedRevision = m_revision,
                        .payload          = SConfigureOutputViewsCommand{.views = std::move(views), .topologyRevision = topologyRevision},
                    })
               .status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::enterWide(LuminophoreWindowKey key) {
    return transact({.expectedRevision = m_revision, .payload = SEnterWideCommand{.key = key}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::exitWide() {
    return transact({.expectedRevision = m_revision, .payload = SExitWideCommand{}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::toggleWide(LuminophoreWindowKey key) {
    return transact({.expectedRevision = m_revision, .payload = SToggleWideCommand{.key = key}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::moveOutputViewImpl(uint64_t outputID, uint64_t expectedTopologyRevision, eLuminophoreSpatialDirection direction) {
    if (expectedTopologyRevision != m_outputTopologyRevision)
        return false;
    const auto next = CLuminophoreOutputViewSolver::move(m_extent, m_outputViews, outputID, direction);
    if (!next || *next == m_outputViews)
        return false;
    m_outputViews = *next;
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::adjustOutputViewImpl(uint64_t outputID, uint64_t expectedTopologyRevision, eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor) {
    if (expectedTopologyRevision != m_outputTopologyRevision)
        return false;
    const auto next = CLuminophoreOutputViewSolver::adjust(m_extent, m_outputViews, outputID, direction, anchor);
    if (!next || *next == m_outputViews)
        return false;
    m_outputViews = *next;
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::configureOutputViewsImpl(std::vector<SLuminophoreOutputView> views, uint64_t topologyRevision) {
    std::ranges::sort(views, {}, &SLuminophoreOutputView::outputID);
    if (!CLuminophoreOutputViewSolver::validate(m_extent, views) || (views == m_outputViews && topologyRevision == m_outputTopologyRevision))
        return false;
    m_outputViews            = std::move(views);
    m_outputTopologyRevision = topologyRevision;
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::configureTopologyImpl(SLuminophoreBoardExtent extent, std::vector<uint64_t> outputIDs, uint64_t topologyRevision) {
    if (extent.columns <= 0 || extent.rows <= 0 || outputIDs.empty() || topologyRevision == 0)
        return false;

    CLuminophoreSpatialModel candidate = *this;
    if (candidate.m_extent != extent && !candidate.reconfigureExtentImpl(extent))
        return false;

    const auto views = CLuminophoreOutputViewSolver::reconcile(candidate.m_extent, candidate.m_outputViews, std::move(outputIDs), {.origin = {}, .columns = 1, .rows = 1});
    if (!views)
        return false;
    if (*views == m_outputViews && topologyRevision == m_outputTopologyRevision && extent == m_extent)
        return false;

    m_extent                 = candidate.m_extent;
    m_view                   = candidate.m_view;
    m_tiled                  = std::move(candidate.m_tiled);
    m_tiledCoordinates       = std::move(candidate.m_tiledCoordinates);
    m_floatingHosts          = std::move(candidate.m_floatingHosts);
    m_floatingBoxes          = std::move(candidate.m_floatingBoxes);
    m_floatingByPoint        = std::move(candidate.m_floatingByPoint);
    m_outputViews            = *views;
    m_outputTopologyRevision = topologyRevision;
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::enterWideImpl(LuminophoreWindowKey key) {
    if (m_presentationMode != eLuminophorePresentationMode::NORMAL || key == 0 || !visibleInNormal(key))
        return false;
    m_presentationMode = eLuminophorePresentationMode::WIDE;
    m_wideKey          = key;
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::exitWideImpl() {
    if (m_presentationMode != eLuminophorePresentationMode::WIDE)
        return false;
    m_presentationMode = eLuminophorePresentationMode::NORMAL;
    m_wideKey.reset();
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::visibleInNormal(LuminophoreWindowKey key) const {
    if (m_independent) {
        const auto output = outputFor(key);
        return output && visibleOnOutput(key, *output);
    }
    const auto coordinate = coordinateOf(key);
    if (!coordinate)
        return false;
    if (m_outputViews.empty())
        return m_view.contains(*coordinate);
    return std::ranges::any_of(m_outputViews, [&](const auto& outputView) { return outputView.rect.contains(*coordinate); });
}

bool CLuminophoreSpatialModel::toggleDesktopImpl() {
    if (m_presentationMode == eLuminophorePresentationMode::DESKTOP) {
        m_presentationMode  = m_desktopReturnMode;
        m_desktopReturnMode = eLuminophorePresentationMode::NORMAL;
    } else {
        m_desktopReturnMode = m_presentationMode;
        m_presentationMode  = eLuminophorePresentationMode::DESKTOP;
    }
    ++m_revision;
    return true;
}

void CLuminophoreSpatialModel::normalizeWideState() {
    const auto effectiveMode = m_presentationMode == eLuminophorePresentationMode::DESKTOP ? m_desktopReturnMode : m_presentationMode;
    if (effectiveMode != eLuminophorePresentationMode::WIDE || (m_wideKey && m_tiledCoordinates.contains(*m_wideKey)))
        return;
    if (m_presentationMode == eLuminophorePresentationMode::DESKTOP)
        m_desktopReturnMode = eLuminophorePresentationMode::NORMAL;
    else
        m_presentationMode = eLuminophorePresentationMode::NORMAL;
    m_wideKey.reset();
}

bool CLuminophoreSpatialModel::adjustViewImpl(eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor) {
    if (!m_view.contains(anchor))
        return false;

    const auto previous = m_view;
    switch (direction) {
        case eLuminophoreSpatialDirection::LEFT:
            if (anchor.x < m_view.origin.x + m_view.columns - 1)
                --m_view.columns;
            else if (m_view.origin.x > 0) {
                --m_view.origin.x;
                ++m_view.columns;
            }
            break;
        case eLuminophoreSpatialDirection::RIGHT:
            if (anchor.x > m_view.origin.x) {
                ++m_view.origin.x;
                --m_view.columns;
            } else if (m_view.origin.x + m_view.columns < m_extent.columns)
                ++m_view.columns;
            break;
        case eLuminophoreSpatialDirection::UP:
            if (anchor.y < m_view.origin.y + m_view.rows - 1)
                --m_view.rows;
            else if (m_view.origin.y > 0) {
                --m_view.origin.y;
                ++m_view.rows;
            }
            break;
        case eLuminophoreSpatialDirection::DOWN:
            if (anchor.y > m_view.origin.y) {
                ++m_view.origin.y;
                --m_view.rows;
            } else if (m_view.origin.y + m_view.rows < m_extent.rows)
                ++m_view.rows;
            break;
    }

    clampView();
    if (m_view == previous)
        return false;
    ++m_revision;
    return true;
}

std::optional<uint64_t> CLuminophoreSpatialModel::outputContaining(const SLuminophoreBoardPoint& point) const {
    const auto view = std::ranges::find_if(m_outputViews, [&](const auto& candidate) { return candidate.rect.contains(point); });
    return view == m_outputViews.end() ? std::nullopt : std::optional<uint64_t>{view->outputID};
}

bool CLuminophoreSpatialModel::visibleOnOutput(LuminophoreWindowKey key, uint64_t outputID) const {
    if (m_independent && outputFor(key) != outputID)
        return false;
    const auto coordinate = coordinateOf(key);
    const auto view       = std::ranges::find(m_outputViews, outputID, &SLuminophoreOutputView::outputID);
    return coordinate && view != m_outputViews.end() && view->rect.contains(*coordinate);
}

void CLuminophoreSpatialModel::setOutputAnchor(uint64_t outputID, std::optional<LuminophoreWindowKey> key) {
    const auto view = std::ranges::find(m_outputViews, outputID, &SLuminophoreOutputView::outputID);
    if (view != m_outputViews.end())
        view->anchorKey = key && visibleOnOutput(*key, outputID) ? key : std::nullopt;
}

void CLuminophoreSpatialModel::normalizeOutputAnchors() {
    for (auto& view : m_outputViews) {
        if (view.anchorKey && !visibleOnOutput(*view.anchorKey, view.outputID))
            view.anchorKey.reset();
    }
}

bool CLuminophoreSpatialModel::resetDefaultView(const SLuminophoreBoardPoint& anchor) {
    return transact({.expectedRevision = m_revision, .payload = SResetViewCommand{.anchor = anchor}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::resetDefaultViewImpl(const SLuminophoreBoardPoint& anchor) {
    const auto previous = m_view;
    m_view.columns      = std::min(2, m_extent.columns);
    m_view.rows         = 1;
    m_view.origin       = {
        .x = std::clamp<int64_t>(anchor.x, 0, m_extent.columns - m_view.columns),
        .y = std::clamp<int64_t>(anchor.y, 0, m_extent.rows - m_view.rows),
    };
    if (m_view == previous)
        return false;
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::reconfigureExtent(SLuminophoreBoardExtent extent) {
    return transact({.expectedRevision = m_revision, .payload = SReconfigureExtentCommand{.extent = extent}}).status == eLuminophoreSpatialTransactionStatus::APPLIED;
}

bool CLuminophoreSpatialModel::reconfigureExtentImpl(SLuminophoreBoardExtent extent) {
    extent = {.columns = std::max(1, extent.columns), .rows = std::max(1, extent.rows)};
    if (extent == m_extent)
        return false;

    const auto previousTiled    = m_tiled;
    const auto previousFloating = m_floatingHosts;
    const auto previousBoxes    = m_floatingBoxes;
    m_extent                    = extent;
    clampView();

    m_tiled.clear();
    m_tiledCoordinates.clear();
    for (const auto& [point, key] : previousTiled) {
        const auto target = m_extent.contains(point) && !m_tiled.contains(point) ? std::optional<SLuminophoreBoardPoint>{point} : firstVacant();
        if (!target)
            break;
        m_tiled[*target]        = key;
        m_tiledCoordinates[key] = *target;
    }

    m_floatingHosts.clear();
    m_floatingBoxes.clear();
    m_floatingByPoint.clear();
    for (const auto& [key, point] : previousFloating) {
        const SLuminophoreBoardPoint host = {
            .x = std::clamp<int64_t>(point.x, 0, m_extent.columns - 1),
            .y = std::clamp<int64_t>(point.y, 0, m_extent.rows - 1),
        };
        m_floatingHosts[key] = host;
        m_floatingBoxes[key] = previousBoxes.at(key);
        m_floatingByPoint[host].emplace_back(key);
    }
    ++m_revision;
    return true;
}

bool CLuminophoreSpatialModel::observeWindowImpl(LuminophoreWindowKey key, eLuminophoreWindowPlacementMode mode, std::optional<SLuminophoreBoardPoint> preferred, std::optional<SLuminophoreNormalizedBox> localBox) {
    if (mode == eLuminophoreWindowPlacementMode::ABSENT) {
        bool changed = false;
        if (const auto tiled = m_tiledCoordinates.find(key); tiled != m_tiledCoordinates.end()) {
            m_tiled.erase(tiled->second);
            m_tiledCoordinates.erase(tiled);
            changed = true;
        }
        if (m_floatingHosts.contains(key)) {
            eraseFloating(key);
            changed = true;
        }
        if (changed)
            ++m_revision;
        return changed;
    }

    if (mode == eLuminophoreWindowPlacementMode::TILED) {
        if (m_tiledCoordinates.contains(key))
            return false;
        const auto target = preferred && m_extent.contains(*preferred) && !m_tiled.contains(*preferred) ? preferred : firstVacant();
        if (!target)
            return false;
        eraseFloating(key);
        m_tiled[*target]        = key;
        m_tiledCoordinates[key] = *target;
        ++m_revision;
        return true;
    }

    if (mode != eLuminophoreWindowPlacementMode::FLOATING || !preferred || !m_extent.contains(*preferred))
        return false;
    const auto box = localBox.value_or(SLuminophoreNormalizedBox{});
    if (const auto found = m_floatingHosts.find(key); found != m_floatingHosts.end() && found->second == *preferred && m_floatingBoxes.at(key) == box)
        return false;
    if (const auto tiled = m_tiledCoordinates.find(key); tiled != m_tiledCoordinates.end()) {
        m_tiled.erase(tiled->second);
        m_tiledCoordinates.erase(tiled);
    }
    eraseFloating(key);
    m_floatingHosts[key] = *preferred;
    m_floatingBoxes[key] = box;
    m_floatingByPoint[*preferred].emplace_back(key);
    ++m_revision;
    return true;
}

void CLuminophoreSpatialModel::eraseFloating(LuminophoreWindowKey key) {
    const auto found = m_floatingHosts.find(key);
    if (found == m_floatingHosts.end())
        return;
    auto& windows = m_floatingByPoint[found->second];
    std::erase(windows, key);
    if (windows.empty())
        m_floatingByPoint.erase(found->second);
    m_floatingHosts.erase(found);
    m_floatingBoxes.erase(key);
}

SLuminophoreBoardPoint CLuminophoreSpatialModel::adjacent(const SLuminophoreBoardPoint& point, eLuminophoreSpatialDirection direction) const {
    switch (direction) {
        case eLuminophoreSpatialDirection::UP: return {.x = point.x, .y = point.y - 1};
        case eLuminophoreSpatialDirection::RIGHT: return {.x = point.x + 1, .y = point.y};
        case eLuminophoreSpatialDirection::DOWN: return {.x = point.x, .y = point.y + 1};
        case eLuminophoreSpatialDirection::LEFT: return {.x = point.x - 1, .y = point.y};
    }
    return point;
}

std::optional<SLuminophoreBoardPoint> CLuminophoreSpatialModel::firstVacant() const {
    for (int y = m_view.origin.y; y < m_view.origin.y + m_view.rows; ++y) {
        for (int x = m_view.origin.x; x < m_view.origin.x + m_view.columns; ++x) {
            const SLuminophoreBoardPoint point = {.x = x, .y = y};
            if (!m_tiled.contains(point))
                return point;
        }
    }

    for (int y = 0; y < m_extent.rows; ++y) {
        for (int x = 0; x < m_extent.columns; ++x) {
            const SLuminophoreBoardPoint point = {.x = x, .y = y};
            if (!m_tiled.contains(point))
                return point;
        }
    }
    return std::nullopt;
}

std::optional<SLuminophoreBoardPoint> CLuminophoreSpatialModel::relocationFor(const SLuminophoreBoardPoint& occupied, const SLuminophoreBoardPoint& source) const {
    constexpr eLuminophoreSpatialDirection ORDER[] = {
        eLuminophoreSpatialDirection::RIGHT,
        eLuminophoreSpatialDirection::LEFT,
        eLuminophoreSpatialDirection::UP,
        eLuminophoreSpatialDirection::DOWN,
    };
    for (const auto direction : ORDER) {
        const auto candidate = adjacent(occupied, direction);
        if (candidate != source && m_extent.contains(candidate) && !m_tiled.contains(candidate))
            return candidate;
    }
    return std::nullopt;
}

bool CLuminophoreSpatialModel::pushChain(TiledByPoint& state, const SLuminophoreBoardPoint& point, eLuminophoreSpatialDirection direction) const {
    return luminophoreSpatialPushChain(state, point, [this, direction](const SLuminophoreBoardPoint& cursor) -> std::optional<SLuminophoreBoardPoint> {
        const auto next = adjacent(cursor, direction);
        return m_extent.contains(next) ? std::optional{next} : std::nullopt;
    });
}

void CLuminophoreSpatialModel::clampView() {
    m_view.columns  = std::clamp<int64_t>(m_view.columns, 1, m_extent.columns);
    m_view.rows     = std::clamp<int64_t>(m_view.rows, 1, m_extent.rows);
    m_view.origin.x = std::clamp<int64_t>(m_view.origin.x, 0, m_extent.columns - m_view.columns);
    m_view.origin.y = std::clamp<int64_t>(m_view.origin.y, 0, m_extent.rows - m_view.rows);
}

void CLuminophoreSpatialModel::commitTiled(TiledByPoint&& next) {
    m_tiled = std::move(next);
    m_tiledCoordinates.clear();
    for (const auto& [point, key] : m_tiled)
        m_tiledCoordinates[key] = point;
    ++m_revision;
}
