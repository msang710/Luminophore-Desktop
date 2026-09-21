#include "../../src/luminophore/LuminophoreSpatialBoard.hpp"
#include <gtest/gtest.h>
using namespace Luminophore::Spatial;

static SState initial() {
    SState s;
    s.boards.emplace(1, SBoard{.view = {{0, 0}, 2, 2}, .defaultColumns = 2, .defaultRows = 2});
    s.boards.emplace(2, SBoard{.view = {{0, 0}, 2, 2}});
    return s;
}
TEST(LuminophoreSpatialBoard, SpawnKeepsFocusAndPushesOnlyItsChain) {
    auto s               = initial();
    s.boards.at(1).tiled = {{{0, 0}, 1}, {{1, 0}, 2}, {{1, 1}, 3}};
    s.focus              = 1;
    const auto r         = transact(s, 0, SCreate{1, 4});
    ASSERT_EQ(r.status, eStatus::APPLIED);
    const auto& b = r.state.boards.at(1);
    EXPECT_EQ(b.tiled.at({0, 0}), 1);
    EXPECT_EQ(b.tiled.at({1, 0}), 4);
    EXPECT_EQ(b.tiled.at({2, 0}), 2);
    EXPECT_EQ(b.tiled.at({1, 1}), 3);
    EXPECT_EQ(r.state.focus, 1);
    EXPECT_EQ(b.view.columns, 3);
    EXPECT_EQ(b.lastChange, eViewOrigin::AUTO);
    EXPECT_EQ(r.state.boards.at(2), s.boards.at(2));
}
TEST(LuminophoreSpatialBoard, CrossBoardSwapDoesNotPushOthersOrChangeViews) {
    auto s               = initial();
    s.boards.at(1).tiled = {{{0, 0}, 1}, {{1, 0}, 3}};
    s.boards.at(2).tiled = {{{0, 0}, 2}};
    auto r               = transact(s, 0, SMove{1, 2, {0, 0}});
    ASSERT_EQ(r.status, eStatus::APPLIED);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({0, 0}), 2);
    EXPECT_EQ(r.state.boards.at(2).tiled.at({0, 0}), 1);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({1, 0}), 3);
    EXPECT_EQ(r.state.boards.at(1).view, s.boards.at(1).view);
}
TEST(LuminophoreSpatialBoard, CloseOnlyShrinksAutoAndNeverBelowDefault) {
    auto  s        = initial();
    auto& b        = s.boards.at(1);
    b.view.columns = 3;
    b.tiled        = {{{0, 0}, 1}, {{2, 0}, 2}};
    auto r         = transact(s, 0, SRemove{2});
    EXPECT_EQ(r.state.boards.at(1).view.columns, 3);
    b.lastChange = eViewOrigin::AUTO;
    r            = transact(s, 0, SRemove{2, eRemovalCause::MINIMIZE});
    EXPECT_EQ(r.state.boards.at(1).view.columns, 3);
    r = transact(s, 0, SRemove{2});
    EXPECT_EQ(r.state.boards.at(1).view.columns, 2);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({0, 0}), 1);
    auto empty = transact(r.state, r.state.revision, SRemove{1});
    EXPECT_EQ(empty.state.boards.at(1).view.columns, 2);
}
TEST(LuminophoreSpatialBoard, OverflowIsAtomicAndStaleDoesNotMutate) {
    auto s               = initial();
    s.boards.at(1).tiled = {{{INT64_MAX, 0}, 1}};
    s.focus              = 1;
    auto r               = transact(s, 0, SCreate{1, 2});
    EXPECT_EQ(r.status, eStatus::OVERFLOW);
    EXPECT_EQ(r.state, s);
    EXPECT_EQ(transact(s, 3, SCreate{1, 2}).status, eStatus::STALE);
    EXPECT_FALSE(checkedAdd(INT64_MIN, -1));
    EXPECT_FALSE(checkedAdd(INT64_MAX, 1));
}
TEST(LuminophoreSpatialBoard, NegativeAndDistantCoordinatesDoNotAllocateAnExtent) {
    auto s              = initial();
    s.boards.at(1).view = {{-1'000'000'000'000LL, -10}, 2, 3};
    auto r              = transact(s, 0, SCreate{1, 1});
    ASSERT_EQ(r.status, eStatus::APPLIED);
    EXPECT_EQ(r.state.boards.at(1).tiled.begin()->first.x, -1'000'000'000'000LL);
    EXPECT_EQ(r.state.boards.at(1).tiled.size(), 1);
}
TEST(LuminophoreSpatialBoard, ViewAspectAlternatesAxisWithoutChangingFocus) {
    auto s               = initial();
    s.boards.at(1).view  = {{0, 0}, 1, 1};
    s.boards.at(1).tiled = {{{0, 0}, 1}};
    s.focus              = 1;
    auto r               = transact(s, 0, SCreate{1, 2});
    ASSERT_EQ(r.status, eStatus::APPLIED);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({1, 0}), 2);
    r = transact(r.state, r.state.revision, SCreate{1, 3});
    ASSERT_EQ(r.status, eStatus::APPLIED);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({0, 1}), 3);
    EXPECT_EQ(r.state.focus, 1);
}
TEST(LuminophoreSpatialBoard, DuplicateInvalidFocusAndSameViewAreAtomic) {
    auto s               = initial();
    s.boards.at(1).tiled = {{{0, 0}, 1}};
    EXPECT_EQ(transact(s, 0, SCreate{1, 1}).state, s);
    EXPECT_EQ(transact(s, 0, SSetFocus{999}).state, s);
    EXPECT_EQ(transact(s, 0, SSetView{1, s.boards.at(1).view}).status, eStatus::NO_CHANGE);
    EXPECT_EQ(transact(s, 0, SMove{1, 999, {0, 0}}).state, s);
    s.revision = UINT64_MAX;
    EXPECT_EQ(transact(s, s.revision, SCreate{1, 2}).state, s);
}

TEST(LuminophoreSpatialBoard, CausalReferenceAlwaysInsertsAndPushesRight) {
    for (const auto view : {SRect{{0, 0}, 5, 1}, SRect{{0, 0}, 1, 5}}) {
        auto  s     = initial();
        auto& b     = s.boards.at(1);
        b.view      = view;
        b.tiled     = {{{0, 0}, 1}, {{1, 0}, 2}, {{2, 0}, 3}};
        s.focus     = 3;
        auto result = transact(s, 0, SCreate{1, 4, SPoint{0, 0}});
        ASSERT_EQ(result.status, eStatus::APPLIED);
        const auto& placed = result.state.boards.at(1);
        EXPECT_EQ(placed.tiled.at({0, 0}), 1);
        EXPECT_EQ(placed.tiled.at({1, 0}), 4);
        EXPECT_EQ(placed.tiled.at({2, 0}), 2);
        EXPECT_EQ(placed.tiled.at({3, 0}), 3);
        EXPECT_EQ(result.state.focus, 3);
    }
}
TEST(LuminophoreSpatialBoard, DefaultWithoutReferenceRetainsAspectBasedInsertion) {
    auto s               = initial();
    s.boards.at(1).view  = {{0, 0}, 5, 1};
    s.boards.at(1).tiled = {{{0, 0}, 1}};
    s.focus              = 1;
    auto result          = transact(s, 0, SCreate{1, 2});
    ASSERT_EQ(result.status, eStatus::APPLIED);
    EXPECT_EQ(result.state.boards.at(1).tiled.at({0, 1}), 2);
}

TEST(LuminophoreSpatialBoard, InitialDirectionUsesViewBoundaryEvenOnEmptyBoard) {
    for (const auto& [direction, point] : std::vector<std::pair<std::string, SPoint>>{{"right", {5, -2}}, {"left", {1, -2}}, {"up", {3, -4}}, {"down", {3, 0}}}) {
        auto s              = initial();
        s.boards.at(1).view = {{2, -3}, 3, 3};
        const auto r        = transact(s, 0, SCreate{1, 1, std::nullopt, direction});
        ASSERT_EQ(r.status, eStatus::APPLIED);
        EXPECT_EQ(r.state.boards.at(1).tiled.at(point), 1);
        EXPECT_TRUE(r.state.boards.at(1).view.contains(point));
        EXPECT_TRUE(r.state.boards.at(1).view.contains({2, -3}));
        EXPECT_TRUE(r.state.boards.at(1).view.contains({4, -1}));
    }
}
TEST(LuminophoreSpatialBoard, LeftPlacementPushesOutsideChainAndRejectsBadDirection) {
    auto s               = initial();
    s.boards.at(1).tiled = {{{-1, 0}, 1}, {{-2, 0}, 2}};
    auto r               = transact(s, 0, SCreate{1, 3, std::nullopt, "left"});
    ASSERT_EQ(r.status, eStatus::APPLIED);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({-1, 0}), 3);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({-2, 0}), 1);
    EXPECT_EQ(r.state.boards.at(1).tiled.at({-3, 0}), 2);
    EXPECT_EQ(transact(s, 0, SCreate{1, 3, std::nullopt, "bad"}).status, eStatus::INVALID);
}

TEST(LuminophoreSpatialBoard, InitialDirectionOverflowIsAtomic) {
    auto s              = initial();
    s.boards.at(1).view = {{INT64_MIN, 0}, 1, 1};
    const auto r        = transact(s, 0, SCreate{1, 1, std::nullopt, "left"});
    EXPECT_EQ(r.status, eStatus::OVERFLOW);
    EXPECT_EQ(r.state, s);
}
