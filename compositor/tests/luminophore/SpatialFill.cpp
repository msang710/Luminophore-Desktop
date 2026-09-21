#include "../../src/luminophore/LuminophoreSpatialFill.hpp"
#include <gtest/gtest.h>
#include <random>
using namespace Luminophore::Spatial;
static std::optional<WindowKey> at(const SFill& fill, SPoint p) {
    for (const auto& r : fill.rectangles)
        if (r.logical.contains(p))
            return r.owner;
    return std::nullopt;
}
TEST(LuminophoreSpatialFill, FocusOnlyChangesEmptyOwnershipAndIgnoresOutside) {
    SBoard b{.view = {{0, 0}, 2, 2}};
    b.tiled = {{{0, 0}, 1}, {{1, 0}, 2}, {{0, 1}, 3}, {{-1, 0}, 4}};
    EXPECT_EQ(at(computeOwnership(b, 3), {1, 1}), 3);
    EXPECT_EQ(at(computeOwnership(b, 2), {1, 1}), 2);
    b.tiled   = {{{1, 1}, 1}, {{-1, 0}, 4}};
    auto fill = computeOwnership(b, 4);
    EXPECT_FALSE(at(fill, {0, 0}));
    EXPECT_FALSE(at(fill, {1, 0}));
    EXPECT_EQ(at(fill, {1, 1}), 1);
}
TEST(LuminophoreSpatialFill, SparseRectangleCandidatesMatchDenseEnumeration) {
    std::mt19937 rng(1729);
    for (int trial = 0; trial < 500; ++trial) {
        SBoard    b{.view = {{-3, -2}, 8, 7}};
        WindowKey key = 1;
        for (int y = -3; y < 6; ++y)
            for (int x = -4; x < 6; ++x)
                if (rng() % 7 == 0)
                    b.tiled[{x, y}] = key++;
        const std::optional<WindowKey>             focus = rng() % key;
        std::map<SPoint, std::optional<WindowKey>> dense;
        auto                                       fill = computeOwnership(b, focus);
        ASSERT_EQ(fill.status, eFillStatus::OK);
        for (int y = -2; y < 5; ++y)
            for (int x = -3; x < 5; ++x) {
                const auto occupied = b.tiled.find({x, y});
                const auto top      = dense[{x, y - 1}];
                const auto left     = dense[{x - 1, y}];
                auto       owner    = occupied != b.tiled.end() ? std::optional{occupied->second} : left;
                auto       distance = [&](WindowKey k) {
                    for (const auto& [p, window] : b.tiled)
                        if (window == k)
                            return x - p.x + y - p.y;
                    return int64_t{999};
                };
                if (occupied == b.tiled.end() && top && (!owner || distance(*top) < distance(*owner) || (distance(*top) == distance(*owner) && top == focus)))
                    owner = top;
                dense[{x, y}] = owner;
            }
        std::map<SPoint, std::optional<WindowKey>> expected;
        for (const auto& [point, k] : b.tiled) {
            if (!b.view.contains(point))
                continue;
            int64_t bestArea = 0, bestWidth = 0, bestHeight = 0;
            for (int64_t h = 1; h <= 5 - point.y; ++h)
                for (int64_t w = 1; w <= 5 - point.x; ++w) {
                    bool allowed = true;
                    for (int64_t y = point.y; y < point.y + h; ++y)
                        for (int64_t x = point.x; x < point.x + w; ++x)
                            allowed = allowed && dense[{x, y}] == k;
                    if (allowed && (w * h > bestArea || (w * h == bestArea && w > bestWidth))) {
                        bestArea   = w * h;
                        bestWidth  = w;
                        bestHeight = h;
                    }
                }
            for (int64_t y = point.y; y < point.y + bestHeight; ++y)
                for (int64_t x = point.x; x < point.x + bestWidth; ++x)
                    expected[{x, y}] = k;
        }
        // Dense recovery oracle: enumerate all supersets of each fixed rectangle.
        std::vector<std::pair<SPoint, WindowKey>> recovery;
        for (const auto& entry : b.tiled)
            if (b.view.contains(entry.first))
                recovery.push_back(entry);
        std::sort(recovery.begin(), recovery.end(), [&](const auto& a, const auto& b) {
            const auto da = a.first.x + a.first.y, db = b.first.x + b.first.y;
            if (da != db)
                return da > db;
            if ((a.second == focus) != (b.second == focus))
                return a.second == focus;
            return a.first.x < b.first.x;
        });
        for (const auto& [point, k] : recovery) {
            int64_t minWidth = 1, minHeight = 1;
            for (const auto& [p, owner] : expected)
                if (owner == k) {
                    minWidth  = std::max(minWidth, p.x - point.x + 1);
                    minHeight = std::max(minHeight, p.y - point.y + 1);
                }
            auto bestWidth = minWidth, bestHeight = minHeight;
            for (int64_t h = minHeight; h <= 5 - point.y; ++h)
                for (int64_t w = minWidth; w <= 5 - point.x; ++w) {
                    bool allowed = true;
                    for (int64_t y = point.y; y < point.y + h; ++y)
                        for (int64_t x = point.x; x < point.x + w; ++x) {
                            const auto owner = expected[{x, y}];
                            allowed          = allowed && (!owner || owner == k);
                        }
                    if (allowed && (w * h > bestWidth * bestHeight || (w * h == bestWidth * bestHeight && w > bestWidth))) {
                        bestWidth  = w;
                        bestHeight = h;
                    }
                }
            for (int64_t y = point.y; y < point.y + bestHeight; ++y)
                for (int64_t x = point.x; x < point.x + bestWidth; ++x)
                    expected[{x, y}] = k;
        }
        for (int y = -2; y < 5; ++y)
            for (int x = -3; x < 5; ++x)
                EXPECT_EQ(at(fill, {x, y}), expected[(SPoint{x, y})]) << trial << ":" << x << "," << y;
    }
}
TEST(LuminophoreSpatialFill, WorkDependsOnOccupiedBreaksNotCoordinateDistance) {
    SBoard b{.view = {{-1'000'000'000'000LL, 0}, 2'000'000'000'000LL, 1'000'000'000}};
    b.tiled   = {{{0, 0}, 1}};
    auto fill = computeOwnership(b, 1);
    ASSERT_EQ(fill.status, eFillStatus::OK);
    EXPECT_LE(fill.visited, 6);
    EXPECT_EQ(at(fill, {999'999'999'999LL, 999'999'999}), 1);
    EXPECT_EQ(computeOwnership(b, 1, 1).status, eFillStatus::RESOURCE_LIMIT);
}

TEST(LuminophoreSpatialFill, SideBySideKeepTheirOwnColumnsRegardlessOfFocus) {
    SBoard b{.view = {{0, 0}, 2, 1000000}};
    b.tiled = {{{0, 0}, 1}, {{1, 0}, 2}};
    for (auto focus : {1UL, 2UL}) {
        const auto fill = computeOwnership(b, focus);
        EXPECT_EQ(at(fill, {0, 999999}), 1);
        EXPECT_EQ(at(fill, {1, 999999}), 2);
    }
}

TEST(LuminophoreSpatialFill, JointHorizontalAndVerticalCandidatesFollowFocus) {
    SBoard b{.view = {{0, 0}, 2, 3}};
    b.tiled = {{{1, 0}, 2}, {{0, 1}, 1}};
    for (int repeat = 0; repeat < 10; ++repeat) {
        for (const auto focus : {1UL, 2UL}) {
            const auto fill = computeOwnership(b, focus);
            ASSERT_EQ(fill.status, eFillStatus::OK);
            EXPECT_FALSE(at(fill, {0, 0}));
            EXPECT_EQ(at(fill, {1, 0}), 2);
            for (int y = 1; y < 3; ++y) {
                EXPECT_EQ(at(fill, {0, y}), 1);
                EXPECT_EQ(at(fill, {1, y}), focus);
            }
        }
    }
}

TEST(LuminophoreSpatialFill, ExhaustiveSmallBoardsKeepCoresAndSingleRectangles) {
    for (unsigned mask = 1; mask < 512; ++mask) {
        SBoard b{.view = {{-1, -1}, 3, 3}};
        for (unsigned i = 0; i < 9; ++i)
            if (mask & (1U << i))
                b.tiled[{int(i % 3) - 1, int(i / 3) - 1}] = i + 1;
        for (const auto& [core, focus] : b.tiled) {
            const auto fill = computeOwnership(b, focus);
            ASSERT_EQ(fill.status, eFillStatus::OK);
            for (const auto& [origin, key] : b.tiled) {
                EXPECT_EQ(at(fill, origin), key);
                int64_t right = origin.x, bottom = origin.y, count = 0;
                for (int y = -1; y < 2; ++y)
                    for (int x = -1; x < 2; ++x)
                        if (at(fill, {x, y}) == key) {
                            EXPECT_GE(x, origin.x);
                            EXPECT_GE(y, origin.y);
                            right  = std::max(right, int64_t(x));
                            bottom = std::max(bottom, int64_t(y));
                            ++count;
                        }
                EXPECT_EQ(count, (right - origin.x + 1) * (bottom - origin.y + 1));
            }
        }
    }
}

TEST(LuminophoreSpatialFill, UnusedFocusedClaimDoesNotShrinkLeftCompetitor) {
    // [ ][F][F]   F keeps its horizontal rectangle; A retains the vacancy.
    // [A][A][B]
    for (const auto offset : {0, 100}) {
        SBoard          b{.view = {{0, 0}, 3, 2}};
        const WindowKey a = 1 + offset, f = 2 + offset, blocker = 3 + offset;
        b.tiled = {{{1, 0}, f}, {{0, 1}, a}, {{2, 1}, blocker}};
        for (int repeat = 0; repeat < 5; ++repeat)
            for (const auto focus : {a, f, blocker}) {
                const auto fill = computeOwnership(b, focus);
                ASSERT_EQ(fill.status, eFillStatus::OK);
                EXPECT_FALSE(at(fill, {0, 0}));
                EXPECT_EQ(at(fill, {1, 0}), f);
                EXPECT_EQ(at(fill, {2, 0}), f);
                EXPECT_EQ(at(fill, {0, 1}), a);
                EXPECT_EQ(at(fill, {1, 1}), a);
                EXPECT_EQ(at(fill, {2, 1}), blocker);
            }
    }
}
