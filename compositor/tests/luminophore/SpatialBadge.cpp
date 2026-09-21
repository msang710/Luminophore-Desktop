#include "../../src/luminophore/LuminophoreSpatialBadge.hpp"
#include <gtest/gtest.h>

TEST(LuminophoreSpatialBadge, FollowsSubcellPointerMotionAndNegativeOutputCoordinates) {
    const auto first = Luminophore::SpatialBadge::target({50, 50}, {32, 32});
    const auto next  = Luminophore::SpatialBadge::target({51.25, 53.5}, {32, 32});
    EXPECT_DOUBLE_EQ(next.x - first.x, 1.25);
    EXPECT_DOUBLE_EQ(next.y - first.y, 3.5);
    const auto outside = Luminophore::SpatialBadge::target({-1920, -500}, {32, 32});
    EXPECT_EQ(outside.pos(), (Vector2D{-1936, -516}));
    EXPECT_EQ(outside.size(), first.size());
}

TEST(LuminophoreSpatialBadge, ShrinkUsesCurrentPointerAndClampedExistingProgress) {
    const CBox source{100, 100, 800, 600};
    const auto destination = Luminophore::SpatialBadge::target({900, 700}, {32, 32});
    EXPECT_EQ(Luminophore::SpatialBadge::transition(source, destination, -1), source);
    EXPECT_EQ(Luminophore::SpatialBadge::transition(source, destination, 2), destination);
    const auto halfway = Luminophore::SpatialBadge::transition(source, destination, .5F);
    EXPECT_EQ(halfway.size(), (Vector2D{416, 316}));
    const auto moved = Luminophore::SpatialBadge::transition(source, Luminophore::SpatialBadge::target({1000, 800}, {32, 32}), .5F);
    EXPECT_EQ(moved.pos() - halfway.pos(), (Vector2D{50, 50}));
}

TEST(LuminophoreSpatialBadge, OneBadgeSpansAdjacentOutputsWithoutEditorClipping) {
    const auto badge = Luminophore::SpatialBadge::target({1920, 600}, {32, 32});
    const auto left  = badge.intersection(CBox{0, 0, 1920, 1080});
    const auto right = badge.intersection(CBox{1920, 0, 1920, 1080});
    EXPECT_DOUBLE_EQ(left.w, 16);
    EXPECT_DOUBLE_EQ(right.w, 16);
    EXPECT_DOUBLE_EQ(left.h, 32);
    EXPECT_DOUBLE_EQ(right.h, 32);
}
