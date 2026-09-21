#pragma once

#include <optional>
#include <vector>

// Callers own the candidate map. Nothing is changed until the complete path is
// checked, so an overflow or legacy extent failure cannot leave a partial push.
template <typename Map, typename Advance>
bool luminophoreSpatialPushChain(Map& state, const typename Map::key_type& start, Advance advance) {
    using Point = typename Map::key_type;
    std::vector<Point> chain;
    auto               cursor = start;
    while (state.contains(cursor)) {
        chain.push_back(cursor);
        const auto next = advance(cursor);
        if (!next || *next == cursor)
            return false;
        cursor = *next;
    }
    for (auto it = chain.rbegin(); it != chain.rend(); ++it) {
        state[cursor] = state.at(*it);
        state.erase(*it);
        cursor = *it;
    }
    return true;
}
