#include "../../src/luminophore/LuminophoreOutputViewSolver.hpp"
#include "../../src/luminophore/LuminophoreSpatialTransaction.hpp"

#include <gtest/gtest.h>

TEST(LuminophoreOutputViewSolver, RejectsDuplicateOverlappingAndOutOfBoundsViews) {
    const SLuminophoreBoardExtent extent = {.columns = 8, .rows = 4};

    EXPECT_FALSE(CLuminophoreOutputViewSolver::validate(extent,
                                                 {
                                                     {.outputID = 1, .rect = {.origin = {}, .columns = 2, .rows = 2}},
                                                     {.outputID = 1, .rect = {.origin = {.x = 2}, .columns = 2, .rows = 2}},
                                                 }));
    EXPECT_FALSE(CLuminophoreOutputViewSolver::validate(extent,
                                                 {
                                                     {.outputID = 1, .rect = {.origin = {}, .columns = 2, .rows = 2}},
                                                     {.outputID = 2, .rect = {.origin = {.x = 1}, .columns = 2, .rows = 2}},
                                                 }));
    EXPECT_FALSE(CLuminophoreOutputViewSolver::validate(extent, {{.outputID = 1, .rect = {.origin = {.x = 7}, .columns = 2, .rows = 1}}}));
}

TEST(LuminophoreOutputViewSolver, MovesCollidingViewsAsOneDeterministicChain) {
    const SLuminophoreBoardExtent             extent = {.columns = 8, .rows = 2};
    const std::vector<SLuminophoreOutputView> views  = {
        {.outputID = 30, .rect = {.origin = {.x = 4}, .columns = 2, .rows = 1}},
        {.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 1}},
        {.outputID = 20, .rect = {.origin = {.x = 2}, .columns = 2, .rows = 1}},
    };

    const auto result = CLuminophoreOutputViewSolver::move(extent, views, 10, eLuminophoreSpatialDirection::RIGHT);

    ASSERT_TRUE(result);
    EXPECT_EQ(*result,
              (std::vector<SLuminophoreOutputView>{
                  {.outputID = 10, .rect = {.origin = {.x = 1}, .columns = 2, .rows = 1}},
                  {.outputID = 20, .rect = {.origin = {.x = 3}, .columns = 2, .rows = 1}},
                  {.outputID = 30, .rect = {.origin = {.x = 5}, .columns = 2, .rows = 1}},
              }));
}

TEST(LuminophoreOutputViewSolver, RejectsTheWholeChainAtBoardEdge) {
    const SLuminophoreBoardExtent             extent = {.columns = 6, .rows = 1};
    const std::vector<SLuminophoreOutputView> views  = {
        {.outputID = 1, .rect = {.origin = {}, .columns = 2, .rows = 1}},
        {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 2, .rows = 1}},
        {.outputID = 3, .rect = {.origin = {.x = 4}, .columns = 2, .rows = 1}},
    };

    EXPECT_FALSE(CLuminophoreOutputViewSolver::move(extent, views, 1, eLuminophoreSpatialDirection::RIGHT));
    EXPECT_TRUE(CLuminophoreOutputViewSolver::validate(extent, views));
}

TEST(LuminophoreOutputViewSolver, PushesVerticalCollisionWithoutMovingDiagonalNeighbor) {
    const SLuminophoreBoardExtent             extent = {.columns = 5, .rows = 6};
    const std::vector<SLuminophoreOutputView> views  = {
        {.outputID = 1, .rect = {.origin = {}, .columns = 2, .rows = 2}},
        {.outputID = 2, .rect = {.origin = {.x = 0, .y = 2}, .columns = 2, .rows = 2}},
        {.outputID = 3, .rect = {.origin = {.x = 2, .y = 2}, .columns = 2, .rows = 2}},
    };

    const auto result = CLuminophoreOutputViewSolver::move(extent, views, 1, eLuminophoreSpatialDirection::DOWN);

    ASSERT_TRUE(result);
    EXPECT_EQ((*result)[0].rect.origin, (SLuminophoreBoardPoint{.x = 0, .y = 1}));
    EXPECT_EQ((*result)[1].rect.origin, (SLuminophoreBoardPoint{.x = 0, .y = 3}));
    EXPECT_EQ((*result)[2], views[2]);
}

TEST(LuminophoreOutputViewSolver, AdjustsOnlyTheRequestedOutputAndPushesOnExpansion) {
    const SLuminophoreBoardExtent             extent = {.columns = 8, .rows = 3};
    const std::vector<SLuminophoreOutputView> views  = {
        {.outputID = 1, .rect = {.origin = {.x = 1}, .columns = 1, .rows = 1}},
        {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 3, .rows = 1}},
    };

    const auto result = CLuminophoreOutputViewSolver::adjust(extent, views, 1, eLuminophoreSpatialDirection::RIGHT, {.x = 1, .y = 0});

    ASSERT_TRUE(result);
    EXPECT_EQ(*result,
              (std::vector<SLuminophoreOutputView>{
                  {.outputID = 1, .rect = {.origin = {.x = 1}, .columns = 2, .rows = 1}},
                  {.outputID = 2, .rect = {.origin = {.x = 3}, .columns = 3, .rows = 1}},
              }));
}

TEST(LuminophoreOutputViewSolver, ReconcilesTopologyIndependentlyOfEnumerationOrder) {
    const SLuminophoreBoardExtent             extent  = {.columns = 8, .rows = 2};
    const std::vector<SLuminophoreOutputView> current = {
        {.outputID = 20, .rect = {.origin = {.x = 4}, .columns = 2, .rows = 1}},
        {.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 1}},
    };
    const SLuminophoreViewRect defaultRect = {.origin = {}, .columns = 2, .rows = 1};

    const auto          forward = CLuminophoreOutputViewSolver::reconcile(extent, current, {10, 20, 30}, defaultRect);
    const auto          reverse = CLuminophoreOutputViewSolver::reconcile(extent, current, {30, 20, 10}, defaultRect);

    ASSERT_TRUE(forward);
    ASSERT_TRUE(reverse);
    EXPECT_EQ(*forward, *reverse);
    EXPECT_EQ((*forward)[0], current[1]);
    EXPECT_EQ((*forward)[1], current[0]);
    EXPECT_EQ((*forward)[2], (SLuminophoreOutputView{.outputID = 30, .rect = {.origin = {.x = 0, .y = 1}, .columns = 2, .rows = 1}}));
}

TEST(LuminophoreOutputViewSolver, RemovesMissingOutputWithoutChangingSurvivingViews) {
    const SLuminophoreBoardExtent             extent  = {.columns = 8, .rows = 2};
    const std::vector<SLuminophoreOutputView> current = {
        {.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 1}},
        {.outputID = 20, .rect = {.origin = {.x = 4}, .columns = 2, .rows = 1}},
    };

    const auto result = CLuminophoreOutputViewSolver::reconcile(extent, current, {20}, {.origin = {}, .columns = 2, .rows = 1});

    ASSERT_TRUE(result);
    ASSERT_EQ(result->size(), 1);
    EXPECT_EQ(result->front(), current[1]);
}

TEST(LuminophoreOutputViewSolver, KeepsPriorPresentationWhenNewTopologyHasNoCapacity) {
    const SLuminophoreBoardExtent             extent  = {.columns = 2, .rows = 1};
    const std::vector<SLuminophoreOutputView> current = {{.outputID = 1, .rect = {.origin = {}, .columns = 2, .rows = 1}}};

    EXPECT_FALSE(CLuminophoreOutputViewSolver::reconcile(extent, current, {1, 2}, {.origin = {}, .columns = 2, .rows = 1}));
}

TEST(LuminophoreSpatialModel, StoresAsymmetricOutputViewsWithoutChangingLegacyProjectionView) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 4});
    ASSERT_TRUE(model.addTiled(11, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(22, SLuminophoreBoardPoint{.x = 2, .y = 0}));
    const auto legacyView = model.view();

    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 2, .rect = {.origin = {}, .columns = 1, .rows = 1}, .anchorKey = 11},
            {.outputID = 1, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 3}, .anchorKey = 22},
        },
        7));

    EXPECT_EQ(model.view(), legacyView);
    EXPECT_EQ(model.outputTopologyRevision(), 7);
    EXPECT_EQ(model.outputViews(),
              (std::vector<SLuminophoreOutputView>{
                  {.outputID = 1, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 3}, .anchorKey = 22},
                  {.outputID = 2, .rect = {.origin = {}, .columns = 1, .rows = 1}, .anchorKey = 11},
              }));
    EXPECT_TRUE(model.validate());
}

TEST(LuminophoreSpatialModel, OutputViewMoveRejectsStaleTopologyAndCommitsOneRevision) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 1, .rect = {.origin = {}, .columns = 2, .rows = 1}},
            {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 2, .rows = 1}},
        },
        9));
    const auto before = model.snapshot();

    const auto stale = model.transact({
        .expectedRevision = before.revision,
        .payload          = SMoveViewCommand{.direction = eLuminophoreSpatialDirection::RIGHT, .outputID = 1, .expectedTopologyRevision = 8},
    });
    EXPECT_EQ(stale.status, eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY);
    EXPECT_EQ(model.snapshot(), before);

    ASSERT_TRUE(model.moveView(eLuminophoreSpatialDirection::RIGHT, 1, 9));
    EXPECT_EQ(model.revision(), before.revision + 1);
    EXPECT_EQ(model.outputViews()[0].rect.origin.x, 1);
    EXPECT_EQ(model.outputViews()[1].rect.origin.x, 3);
}

TEST(LuminophoreSpatialModel, RejectedOutputViewPushKeepsTheWholeSnapshot) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 1, .rect = {.origin = {}, .columns = 2, .rows = 1}},
            {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 2, .rows = 1}},
        },
        3));
    const auto before = model.snapshot();

    EXPECT_FALSE(model.moveView(eLuminophoreSpatialDirection::RIGHT, 1, 3));
    EXPECT_EQ(model.snapshot(), before);
}
