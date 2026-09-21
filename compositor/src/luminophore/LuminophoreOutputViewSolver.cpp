#include "LuminophoreOutputViewSolver.hpp"

#include <algorithm>
#include <cmath>
#include <set>

static bool rectValid(const SLuminophoreBoardExtent& extent, const SLuminophoreViewRect& rect) {
    return rect.columns > 0 && rect.rows > 0 && extent.contains(rect.origin) && static_cast<int64_t>(rect.origin.x) + rect.columns <= extent.columns &&
        static_cast<int64_t>(rect.origin.y) + rect.rows <= extent.rows;
}

static bool overlaps(const SLuminophoreViewRect& lhs, const SLuminophoreViewRect& rhs) {
    return lhs.origin.x < rhs.origin.x + rhs.columns && rhs.origin.x < lhs.origin.x + lhs.columns && lhs.origin.y < rhs.origin.y + rhs.rows &&
        rhs.origin.y < lhs.origin.y + lhs.rows;
}

static SLuminophoreViewRect pushedPast(const SLuminophoreViewRect& obstacle, const SLuminophoreViewRect& pushed, eLuminophoreSpatialDirection direction) {
    auto result = pushed;
    switch (direction) {
        case eLuminophoreSpatialDirection::LEFT: result.origin.x = obstacle.origin.x - pushed.columns; break;
        case eLuminophoreSpatialDirection::RIGHT: result.origin.x = obstacle.origin.x + obstacle.columns; break;
        case eLuminophoreSpatialDirection::UP: result.origin.y = obstacle.origin.y - pushed.rows; break;
        case eLuminophoreSpatialDirection::DOWN: result.origin.y = obstacle.origin.y + obstacle.rows; break;
    }
    return result;
}

static bool pushCollisions(const SLuminophoreBoardExtent& extent, std::vector<SLuminophoreOutputView>& views, size_t movingIndex, const SLuminophoreViewRect& candidate, eLuminophoreSpatialDirection direction,
                           std::set<uint64_t>& active) {
    if (!rectValid(extent, candidate) || !active.insert(views[movingIndex].outputID).second)
        return false;

    views[movingIndex].rect = candidate;
    std::vector<size_t> collisions;
    for (size_t index = 0; index < views.size(); ++index) {
        if (index != movingIndex && overlaps(candidate, views[index].rect))
            collisions.emplace_back(index);
    }
    std::ranges::sort(collisions, [&](size_t lhs, size_t rhs) { return views[lhs].outputID < views[rhs].outputID; });

    for (const auto collision : collisions) {
        if (!overlaps(candidate, views[collision].rect))
            continue;
        const auto pushed = pushedPast(candidate, views[collision].rect, direction);
        if (!pushCollisions(extent, views, collision, pushed, direction, active))
            return false;
    }

    active.erase(views[movingIndex].outputID);
    return true;
}

bool CLuminophoreOutputViewSolver::validate(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views) {
    std::set<uint64_t> outputIDs;
    for (size_t index = 0; index < views.size(); ++index) {
        if (views[index].outputID == 0 || !outputIDs.insert(views[index].outputID).second || !rectValid(extent, views[index].rect))
            return false;
        for (size_t other = index + 1; other < views.size(); ++other) {
            if (overlaps(views[index].rect, views[other].rect))
                return false;
        }
    }
    return true;
}

std::optional<std::vector<SLuminophoreOutputView>> CLuminophoreOutputViewSolver::place(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                                         const SLuminophoreViewRect& candidate) {
    const auto found = std::ranges::find(views, outputID, &SLuminophoreOutputView::outputID);
    if (found == views.end() || !validate(extent, views) || !rectValid(extent, candidate))
        return std::nullopt;
    const int64_t left   = static_cast<int64_t>(candidate.origin.x) - found->rect.origin.x;
    const int64_t right  = left + candidate.columns - found->rect.columns;
    const int64_t top    = static_cast<int64_t>(candidate.origin.y) - found->rect.origin.y;
    const int64_t bottom = top + candidate.rows - found->rect.rows;
    const auto    dx     = std::abs(left) > std::abs(right) ? left : right;
    const auto    dy     = std::abs(top) > std::abs(bottom) ? top : bottom;
    return solve(extent, views, outputID, candidate, luminophoreSpatialDirectionForDelta(dx, dy));
}

std::optional<std::vector<SLuminophoreOutputView>> CLuminophoreOutputViewSolver::move(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                                        eLuminophoreSpatialDirection direction) {
    const auto found = std::ranges::find(views, outputID, &SLuminophoreOutputView::outputID);
    if (found == views.end() || !validate(extent, views))
        return std::nullopt;
    auto candidate = found->rect;
    switch (direction) {
        case eLuminophoreSpatialDirection::LEFT: --candidate.origin.x; break;
        case eLuminophoreSpatialDirection::RIGHT: ++candidate.origin.x; break;
        case eLuminophoreSpatialDirection::UP: --candidate.origin.y; break;
        case eLuminophoreSpatialDirection::DOWN: ++candidate.origin.y; break;
    }
    return solve(extent, views, outputID, candidate, direction);
}

std::optional<std::vector<SLuminophoreOutputView>> CLuminophoreOutputViewSolver::reveal(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                                          const SLuminophoreBoardPoint& point, eLuminophoreSpatialDirection direction) {
    const auto found = std::ranges::find(views, outputID, &SLuminophoreOutputView::outputID);
    if (found == views.end() || !extent.contains(point) || !validate(extent, views))
        return std::nullopt;
    auto candidate     = found->rect;
    candidate.origin.x = std::clamp(candidate.origin.x, point.x - candidate.columns + 1, point.x);
    candidate.origin.y = std::clamp(candidate.origin.y, point.y - candidate.rows + 1, point.y);
    return solve(extent, views, outputID, candidate, direction);
}

std::optional<std::vector<SLuminophoreOutputView>> CLuminophoreOutputViewSolver::adjust(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                                          eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor) {
    const auto found = std::ranges::find(views, outputID, &SLuminophoreOutputView::outputID);
    if (found == views.end() || !validate(extent, views) || !found->rect.contains(anchor))
        return std::nullopt;

    auto candidate = found->rect;
    switch (direction) {
        case eLuminophoreSpatialDirection::LEFT:
            if (anchor.x < candidate.origin.x + candidate.columns - 1)
                --candidate.columns;
            else {
                --candidate.origin.x;
                ++candidate.columns;
            }
            break;
        case eLuminophoreSpatialDirection::RIGHT:
            if (anchor.x > candidate.origin.x) {
                ++candidate.origin.x;
                --candidate.columns;
            } else
                ++candidate.columns;
            break;
        case eLuminophoreSpatialDirection::UP:
            if (anchor.y < candidate.origin.y + candidate.rows - 1)
                --candidate.rows;
            else {
                --candidate.origin.y;
                ++candidate.rows;
            }
            break;
        case eLuminophoreSpatialDirection::DOWN:
            if (anchor.y > candidate.origin.y) {
                ++candidate.origin.y;
                --candidate.rows;
            } else
                ++candidate.rows;
            break;
    }
    if (candidate == found->rect)
        return std::nullopt;
    return solve(extent, views, outputID, candidate, direction);
}

std::optional<std::vector<SLuminophoreOutputView>> CLuminophoreOutputViewSolver::reconcile(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& current,
                                                                             std::vector<uint64_t> outputIDs, const SLuminophoreViewRect& defaultRect) {
    std::ranges::sort(outputIDs);
    outputIDs.erase(std::unique(outputIDs.begin(), outputIDs.end()), outputIDs.end());
    if (outputIDs.empty() || outputIDs.front() == 0 || !rectValid(extent, defaultRect))
        return std::nullopt;

    std::vector<SLuminophoreOutputView> result;
    for (const auto outputID : outputIDs) {
        const auto found = std::ranges::find(current, outputID, &SLuminophoreOutputView::outputID);
        if (found != current.end() && rectValid(extent, found->rect) && std::ranges::none_of(result, [&](const auto& view) { return overlaps(view.rect, found->rect); }))
            result.emplace_back(*found);
    }

    for (const auto outputID : outputIDs) {
        if (std::ranges::any_of(result, [&](const auto& view) { return view.outputID == outputID; }))
            continue;
        std::vector<SLuminophoreBoardPoint> origins;
        for (int y = 0; y <= extent.rows - defaultRect.rows; ++y) {
            for (int x = 0; x <= extent.columns - defaultRect.columns; ++x)
                origins.emplace_back(SLuminophoreBoardPoint{.x = x, .y = y});
        }
        std::ranges::sort(origins, [&](const auto& lhs, const auto& rhs) {
            const int lhsDistance = std::abs(lhs.x - defaultRect.origin.x) + std::abs(lhs.y - defaultRect.origin.y);
            const int rhsDistance = std::abs(rhs.x - defaultRect.origin.x) + std::abs(rhs.y - defaultRect.origin.y);
            if (lhsDistance != rhsDistance)
                return lhsDistance < rhsDistance;
            return lhs < rhs;
        });
        const auto vacant = std::ranges::find_if(origins, [&](const auto& origin) {
            const SLuminophoreViewRect candidate = {.origin = origin, .columns = defaultRect.columns, .rows = defaultRect.rows};
            return std::ranges::none_of(result, [&](const auto& view) { return overlaps(view.rect, candidate); });
        });
        if (vacant == origins.end())
            return std::nullopt;
        result.emplace_back(SLuminophoreOutputView{.outputID = outputID, .rect = {.origin = *vacant, .columns = defaultRect.columns, .rows = defaultRect.rows}});
    }

    std::ranges::sort(result, {}, &SLuminophoreOutputView::outputID);
    return validate(extent, result) ? std::optional{result} : std::nullopt;
}

std::optional<std::vector<SLuminophoreOutputView>> CLuminophoreOutputViewSolver::solve(const SLuminophoreBoardExtent& extent, const std::vector<SLuminophoreOutputView>& views, uint64_t outputID,
                                                                         const SLuminophoreViewRect& candidate, eLuminophoreSpatialDirection direction) {
    auto       result = views;
    const auto found  = std::ranges::find(result, outputID, &SLuminophoreOutputView::outputID);
    if (found == result.end())
        return std::nullopt;
    std::set<uint64_t> active;
    if (!pushCollisions(extent, result, std::distance(result.begin(), found), candidate, direction, active))
        return std::nullopt;
    std::ranges::sort(result, {}, &SLuminophoreOutputView::outputID);
    return validate(extent, result) ? std::optional{result} : std::nullopt;
}
