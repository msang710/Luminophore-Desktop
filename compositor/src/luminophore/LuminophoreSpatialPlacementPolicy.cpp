#include "LuminophoreSpatialPlacementPolicy.hpp"
#include "LuminophoreSpatialPush.hpp"
#include <algorithm>
#include <limits>

using namespace Luminophore::Spatial;
bool Luminophore::Spatial::includePoint(SRect& rect, SPoint point) {
    const auto x      = std::min(rect.origin.x, point.x);
    const auto y      = std::min(rect.origin.y, point.y);
    const auto right  = checkedAdd(point.x, 1);
    const auto bottom = checkedAdd(point.y, 1);
    if (!right || !bottom)
        return false;
    const auto r = std::max(*checkedAdd(rect.origin.x, rect.columns), *right);
    const auto b = std::max(*checkedAdd(rect.origin.y, rect.rows), *bottom);
    // Unsigned subtraction gives the exact nonnegative span, including across zero.
    const auto w = static_cast<uint64_t>(r) - static_cast<uint64_t>(x);
    const auto h = static_cast<uint64_t>(b) - static_cast<uint64_t>(y);
    if (w > INT64_MAX || h > INT64_MAX)
        return false;
    rect = {{x, y}, static_cast<int64_t>(w), static_cast<int64_t>(h)};
    return true;
}
eStatus Luminophore::Spatial::insertWindow(SState& state, const SCreate& cmd) {
    const auto found = state.boards.find(cmd.board);
    if (!cmd.key || found == state.boards.end())
        return eStatus::INVALID;
    for (const auto& [id, b] : state.boards)
        for (const auto& [p, key] : b.tiled)
            if (key == cmd.key)
                return eStatus::INVALID;
    auto& board  = found->second;
    auto  anchor = board.lastAnchor.value_or(board.view.origin);
    for (const auto& [p, key] : board.tiled)
        if (state.focus == key)
            anchor = p;
    if (cmd.reference)
        anchor = *cmd.reference;
    const auto& direction = cmd.initialPlacement;
    const bool  specified = !direction.empty() && direction != "default";
    if (specified && direction != "right" && direction != "left" && direction != "up" && direction != "down")
        return eStatus::INVALID;
    const bool horizontal = specified ? (direction == "right" || direction == "left") : (cmd.reference.has_value() || board.view.rows >= board.view.columns);
    const int  delta      = direction == "left" || direction == "up" ? -1 : 1;
    const auto advance    = [horizontal, delta](SPoint p) -> std::optional<SPoint> {
        const auto n = checkedAdd(horizontal ? p.x : p.y, delta);
        if (!n)
            return std::nullopt;
        if (horizontal)
            p.x = *n;
        else
            p.y = *n;
        return p;
    };
    if (specified) {
        // Start from the middle cell of the requested boundary, at map time.
        const auto x = checkedAdd(board.view.origin.x, horizontal ? (direction == "right" ? board.view.columns - 1 : 0) : (board.view.columns - 1) / 2);
        const auto y = checkedAdd(board.view.origin.y, !horizontal ? (direction == "down" ? board.view.rows - 1 : 0) : (board.view.rows - 1) / 2);
        if (!x || !y)
            return eStatus::OVERFLOW;
        anchor = {*x, *y};
    }
    std::vector<WindowKey> visible;
    for (const auto& [p, key] : board.tiled)
        if (board.view.contains(p))
            visible.push_back(key);
    auto target = std::optional{anchor};
    if (specified || !board.tiled.empty())
        target = advance(anchor);
    if (!target || !luminophoreSpatialPushChain(board.tiled, *target, advance))
        return eStatus::OVERFLOW;
    board.tiled.emplace(*target, cmd.key);
    auto view = board.view;
    if (!includePoint(view, *target))
        return eStatus::OVERFLOW;
    for (const auto& [p, key] : board.tiled)
        if (std::ranges::find(visible, key) != visible.end() && !includePoint(view, p))
            return eStatus::OVERFLOW;
    if (view != board.view) {
        board.view       = view;
        board.lastChange = eViewOrigin::AUTO;
    }
    board.lastAnchor = anchor;
    return eStatus::APPLIED;
}
void Luminophore::Spatial::shrinkAfterClose(SBoard& board, SPoint point) {
    if (board.lastChange != eViewOrigin::AUTO || !board.view.contains(point))
        return;
    auto&      v      = board.view;
    const auto right  = *checkedAdd(v.origin.x, v.columns) - 1;
    const auto bottom = *checkedAdd(v.origin.y, v.rows) - 1;
    if (v.columns > board.defaultColumns && (point.x == v.origin.x || point.x == right)) {
        const bool occupied = std::ranges::any_of(board.tiled, [&](const auto& entry) { return v.contains(entry.first) && entry.first.x == point.x; });
        if (!occupied) {
            if (point.x == v.origin.x)
                ++v.origin.x;
            --v.columns;
        }
    }
    if (v.rows > board.defaultRows && (point.y == v.origin.y || point.y == bottom)) {
        const bool occupied = std::ranges::any_of(board.tiled, [&](const auto& entry) { return v.contains(entry.first) && entry.first.y == point.y; });
        if (!occupied) {
            if (point.y == v.origin.y)
                ++v.origin.y;
            --v.rows;
        }
    }
}
