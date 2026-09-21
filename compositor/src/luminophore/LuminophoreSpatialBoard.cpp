#include "LuminophoreSpatialBoard.hpp"
#include "LuminophoreSpatialPlacementPolicy.hpp"
#include <algorithm>
#include <limits>
#include <set>
#include <type_traits>

using namespace Luminophore::Spatial;
std::optional<int64_t> Luminophore::Spatial::checkedAdd(int64_t a, int64_t b) {
    if ((b > 0 && a > std::numeric_limits<int64_t>::max() - b) || (b < 0 && a < std::numeric_limits<int64_t>::min() - b))
        return std::nullopt;
    return a + b;
}
bool SRect::valid() const {
    return columns > 0 && rows > 0 && checkedAdd(origin.x, columns).has_value() && checkedAdd(origin.y, rows).has_value();
}
bool SRect::contains(SPoint p) const {
    return valid() && p.x >= origin.x && p.y >= origin.y && p.x < *checkedAdd(origin.x, columns) && p.y < *checkedAdd(origin.y, rows);
}
bool Luminophore::Spatial::validate(const SState& state) {
    std::set<WindowKey> keys;
    for (const auto& [id, board] : state.boards) {
        if (!id || !board.view.valid() || board.defaultColumns <= 0 || board.defaultRows <= 0)
            return false;
        for (const auto& [point, key] : board.tiled) {
            if (!key || !keys.insert(key).second)
                return false;
        }
    }
    return !state.focus || keys.contains(*state.focus);
}
SResult Luminophore::Spatial::transact(const SState& state, uint64_t expected, const Command& command) {
    if (expected != state.revision)
        return {eStatus::STALE, state};
    if (!validate(state))
        return {eStatus::INVALID, state};
    auto       next   = state;
    const auto status = std::visit(
        [&](const auto& cmd) -> eStatus {
            using T = std::decay_t<decltype(cmd)>;
            if constexpr (std::is_same_v<T, SCreate>)
                return insertWindow(next, cmd);
            else if constexpr (std::is_same_v<T, SSetView>) {
                auto it = next.boards.find(cmd.board);
                if (it == next.boards.end() || !cmd.view.valid())
                    return eStatus::INVALID;
                if (it->second.view == cmd.view)
                    return eStatus::NO_CHANGE;
                it->second.view       = cmd.view;
                it->second.lastChange = eViewOrigin::USER;
                return eStatus::APPLIED;
            } else if constexpr (std::is_same_v<T, SSetFocus>) {
                next.focus = cmd.key;
                for (auto& [id, board] : next.boards)
                    for (const auto& [point, key] : board.tiled)
                        if (cmd.key == key)
                            board.lastAnchor = point;
                return eStatus::APPLIED;
            } else {
                for (auto& [id, board] : next.boards) {
                    const auto it = std::ranges::find_if(board.tiled, [&](const auto& entry) { return entry.second == cmd.key; });
                    if (it == board.tiled.end())
                        continue;
                    const auto point = it->first;
                    if constexpr (std::is_same_v<T, SRemove>) {
                        board.tiled.erase(it);
                        if (cmd.cause == eRemovalCause::CLOSE)
                            shrinkAfterClose(board, point);
                        if (next.focus == cmd.key)
                            next.focus.reset(); // Native focus successor remains a separate event.
                    } else {
                        auto dest = next.boards.find(cmd.board);
                        if (dest == next.boards.end())
                            return eStatus::INVALID;
                        if (id == cmd.board && point == cmd.target)
                            return eStatus::NO_CHANGE;
                        auto&      target   = dest->second.tiled;
                        const auto occupied = target.find(cmd.target);
                        if (occupied != target.end()) {
                            const auto other = occupied->second;
                            occupied->second = cmd.key;
                            it->second       = other;
                        } else {
                            board.tiled.erase(it);
                            target.emplace(cmd.target, cmd.key);
                        }
                    }
                    return eStatus::APPLIED;
                }
                return eStatus::INVALID;
            }
        },
        command);
    if (status != eStatus::APPLIED)
        return {status, state};
    if (!validate(next))
        return {eStatus::INVALID, state};
    if (next == state)
        return {eStatus::NO_CHANGE, state};
    if (state.revision == std::numeric_limits<uint64_t>::max())
        return {eStatus::OVERFLOW, state};
    ++next.revision;
    return {eStatus::APPLIED, std::move(next)};
}
