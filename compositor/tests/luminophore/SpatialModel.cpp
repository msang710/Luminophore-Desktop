#include "../../src/luminophore/LuminophoreSpatialModel.hpp"

#include <gtest/gtest.h>

TEST(LuminophoreSpatialModel, DerivesFiniteBoardFromPhysicalCanvasRatio) {
    EXPECT_EQ(SLuminophoreBoardExtent::fromPhysicalExtent(3840, 1080), (SLuminophoreBoardExtent{.columns = 15, .rows = 5}));
    EXPECT_EQ(SLuminophoreBoardExtent::fromPhysicalExtent(1920, 1080), (SLuminophoreBoardExtent{.columns = 9, .rows = 5}));
}

TEST(LuminophoreSpatialModel, UsesTwoByOneDefaultViewAndPlacesNewWindowsInViewFirst) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 32, .rows = 9});
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = {}, .columns = 2, .rows = 1}));
    ASSERT_TRUE(model.addTiled(1));
    ASSERT_TRUE(model.addTiled(2));
    ASSERT_TRUE(model.addTiled(3));
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{.x = 0, .y = 0}));
    EXPECT_EQ(model.coordinateOf(2), (SLuminophoreBoardPoint{.x = 1, .y = 0}));
    EXPECT_EQ(model.coordinateOf(3), (SLuminophoreBoardPoint{.x = 2, .y = 0}));
}

TEST(LuminophoreSpatialModel, DirectionalMovePushesAChainAtomically) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 1, .y = 0}));
    const auto before = model.revision();
    EXPECT_EQ(model.moveTiled(1, eLuminophoreSpatialDirection::RIGHT), eLuminophoreSpatialMoveResult::PUSHED);
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{.x = 1, .y = 0}));
    EXPECT_EQ(model.coordinateOf(2), (SLuminophoreBoardPoint{.x = 2, .y = 0}));
    EXPECT_EQ(model.tiledAt({.x = 0, .y = 0}), std::nullopt);
    EXPECT_EQ(model.tiledAt({.x = 3, .y = 0}), std::nullopt);
    EXPECT_EQ(model.revision(), before + 1);
}

TEST(LuminophoreSpatialModel, DirectionalMoveSwapsWhenNoVacancyExists) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 2, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 1, .y = 0}));
    EXPECT_EQ(model.moveTiled(1, eLuminophoreSpatialDirection::RIGHT), eLuminophoreSpatialMoveResult::SWAPPED);
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{.x = 1, .y = 0}));
    EXPECT_EQ(model.coordinateOf(2), (SLuminophoreBoardPoint{.x = 0, .y = 0}));
}

TEST(LuminophoreSpatialModel, ViewEdgeAdjustmentMatchesTheApprovedSequence) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    ASSERT_TRUE(model.resetDefaultView({.x = 1, .y = 0}));
    const SLuminophoreBoardPoint anchor = {.x = 1, .y = 0};
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = {.x = 1, .y = 0}, .columns = 2, .rows = 1}));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::DOWN, anchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = {.x = 1, .y = 0}, .columns = 2, .rows = 2}));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::LEFT, anchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = {.x = 1, .y = 0}, .columns = 1, .rows = 2}));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::LEFT, anchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = {.x = 0, .y = 0}, .columns = 2, .rows = 2}));
}

TEST(LuminophoreSpatialModel, ViewCanShrinkToOneCellThenGrowAcrossBothAxes) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    ASSERT_TRUE(model.resetDefaultView({.x = 1, .y = 0}));
    const SLuminophoreBoardPoint anchor = {.x = 1, .y = 0};

    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::LEFT, anchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = anchor, .columns = 1, .rows = 1}));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::RIGHT, anchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = anchor, .columns = 2, .rows = 1}));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::DOWN, anchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = anchor, .columns = 2, .rows = 2}));
}

TEST(LuminophoreSpatialModel, ViewAdjustmentShrinksTheOppositeSideFromAnInteriorAnchor) {
    auto                  model        = CLuminophoreSpatialModel::finiteFixture({.columns = 5, .rows = 5});
    const SLuminophoreBoardPoint originAnchor = {.x = 0, .y = 0};

    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::RIGHT, originAnchor));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::DOWN, originAnchor));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::DOWN, originAnchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = originAnchor, .columns = 3, .rows = 3}));

    const SLuminophoreBoardPoint interiorAnchor = {.x = 1, .y = 1};
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::LEFT, interiorAnchor));
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::UP, interiorAnchor));
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = originAnchor, .columns = 2, .rows = 2}));
}

TEST(LuminophoreSpatialModel, FloatingWindowsBelongToACellWithoutConsumingItsBoardCoordinate) {
    auto                  model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    const SLuminophoreBoardPoint host  = {.x = 1, .y = 0};
    ASSERT_TRUE(model.addTiled(1, host));
    ASSERT_TRUE(model.attachFloating(10, host));
    ASSERT_TRUE(model.attachFloating(11, host));
    EXPECT_EQ(model.tiledAt(host), 1);
    EXPECT_EQ(model.floatingAt(host), (std::vector<LuminophoreWindowKey>{10, 11}));
    EXPECT_EQ(model.floatingHostOf(10), host);
}

TEST(LuminophoreSpatialModel, BoundsOddAndPortraitPhysicalRatios) {
    EXPECT_EQ(SLuminophoreBoardExtent::fromPhysicalExtent(960, 493), (SLuminophoreBoardExtent{.columns = 10, .rows = 5}));
    EXPECT_EQ(SLuminophoreBoardExtent::fromPhysicalExtent(1080, 1920), (SLuminophoreBoardExtent{.columns = 3, .rows = 5}));
    EXPECT_EQ(SLuminophoreBoardExtent::fromPhysicalExtent(0, 1080), (SLuminophoreBoardExtent{}));
    EXPECT_EQ(SLuminophoreBoardExtent::fromPhysicalExtent(1080, -1), (SLuminophoreBoardExtent{}));
}
