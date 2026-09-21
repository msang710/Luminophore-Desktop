#include "../../src/luminophore/LuminophoreSpatialTransaction.hpp"

#include <gtest/gtest.h>

TEST(LuminophoreSpatialTransaction, RejectsStaleRevisionWithoutChangingState) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    ASSERT_TRUE(model.addTiled(1));
    const auto before = model.snapshot();

    const auto result = model.transact({.expectedRevision = 0, .payload = SMoveViewCommand{.direction = eLuminophoreSpatialDirection::RIGHT}});

    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::STALE_REVISION);
    EXPECT_EQ(result.snapshot, before);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, InvalidCommandIsAtomic) {
    auto       model  = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    const auto before = model.snapshot();

    const auto result = model.transact({.expectedRevision = before.revision, .payload = SAttachFloatingCommand{.key = 10, .host = {.x = 99, .y = 99}}});

    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    EXPECT_EQ(result.snapshot, before);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, SuccessfulCommandPublishesExactlyOneRevision) {
    auto       model  = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    const auto before = model.snapshot();

    const auto result = model.transact({.expectedRevision = before.revision, .payload = SAddTiledCommand{.key = 42}});

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.snapshot.revision, before.revision + 1);
    EXPECT_EQ(model.revision(), before.revision + 1);
    EXPECT_TRUE(model.validate());
}

TEST(LuminophoreSpatialTransaction, RejectedMoveDoesNotPublishPartialState) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 1, .rows = 1});
    ASSERT_TRUE(model.addTiled(1));
    const auto before = model.snapshot();

    const auto result = model.transact({.expectedRevision = before.revision, .payload = SMoveTiledCommand{.key = 1, .direction = eLuminophoreSpatialDirection::RIGHT}});

    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_EQ(result.moveResult, eLuminophoreSpatialMoveResult::REJECTED);
    EXPECT_FALSE(result.updatesFocus);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, MovingFocusedWindowOutsideViewPreservesSeatFocus) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 1, .y = 0}));

    const auto result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SMoveTiledCommand{.key = 2, .direction = eLuminophoreSpatialDirection::RIGHT},
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_FALSE(result.updatesFocus);
    EXPECT_EQ(result.nextFocusedKey, std::nullopt);
    EXPECT_EQ(model.coordinateOf(2), (SLuminophoreBoardPoint{.x = 2, .y = 0}));
    EXPECT_EQ(model.visibleTiled(), (std::vector<LuminophoreWindowKey>{1}));
}

TEST(LuminophoreSpatialTransaction, MovingViewAwayFromFocusedWindowPreservesSeatFocus) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 2, .y = 0}));

    const auto result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SMoveViewCommand{.direction = eLuminophoreSpatialDirection::RIGHT, .focusedKey = 1},
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_FALSE(result.updatesFocus);
    EXPECT_EQ(result.nextFocusedKey, std::nullopt);
    EXPECT_EQ(model.view(), (SLuminophoreViewRect{.origin = {.x = 1, .y = 0}, .columns = 2, .rows = 1}));
}

TEST(LuminophoreSpatialTransaction, MovingViewWithNoVisibleWindowPreservesSeatFocus) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));

    const auto result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SMoveViewCommand{.direction = eLuminophoreSpatialDirection::RIGHT, .focusedKey = 1},
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_FALSE(result.updatesFocus);
    EXPECT_EQ(result.nextFocusedKey, std::nullopt);
}

TEST(LuminophoreSpatialTransaction, WideTogglePreservesNormalOutputViewsByteEquivalent) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 10, .rect = {.origin = {}, .columns = 1, .rows = 1}, .anchorKey = 1},
            {.outputID = 20, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 2}},
        },
        7));
    const auto normalViews = model.snapshot().outputViews;

    ASSERT_TRUE(model.toggleWide(1));
    EXPECT_EQ(model.snapshot().presentationMode, eLuminophorePresentationMode::WIDE);
    EXPECT_EQ(model.snapshot().wideKey, 1);
    EXPECT_EQ(model.snapshot().outputViews, normalViews);
    EXPECT_FALSE(model.moveView(eLuminophoreSpatialDirection::RIGHT, 10, 7));
    EXPECT_FALSE(model.adjustView(eLuminophoreSpatialDirection::DOWN, {.x = 0, .y = 0}, 10, 7));

    ASSERT_TRUE(model.toggleWide(1));
    EXPECT_EQ(model.snapshot().presentationMode, eLuminophorePresentationMode::NORMAL);
    EXPECT_EQ(model.snapshot().wideKey, std::nullopt);
    EXPECT_EQ(model.snapshot().outputViews, normalViews);
}

TEST(LuminophoreSpatialTransaction, WideEntryRequiresAVisibleTiledWindow) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 4, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 1, .rows = 1}}}, 7));
    const auto before = model.snapshot();

    EXPECT_FALSE(model.enterWide(1));
    EXPECT_FALSE(model.enterWide(999));
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, RemovingWideKeyRestoresNormalInTheSameRevision) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.enterWide(1));
    const auto before = model.revision();

    ASSERT_TRUE(model.remove(1));

    EXPECT_EQ(model.revision(), before + 1);
    EXPECT_EQ(model.snapshot().presentationMode, eLuminophorePresentationMode::NORMAL);
    EXPECT_EQ(model.snapshot().wideKey, std::nullopt);
    EXPECT_TRUE(model.validate());
}

TEST(LuminophoreSpatialTransaction, FloatingWideKeyRestoresNormalAtomically) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.enterWide(1));
    const auto before = model.revision();

    const auto result = model.transact({
        .expectedRevision = before,
        .payload          = SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{.x = 0, .y = 0}},
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(model.revision(), before + 1);
    EXPECT_EQ(model.snapshot().presentationMode, eLuminophorePresentationMode::NORMAL);
    EXPECT_EQ(model.floatingHostOf(1), (SLuminophoreBoardPoint{.x = 0, .y = 0}));
}

TEST(LuminophoreSpatialTransaction, TopologyAndOutputViewsCommitAsOneRevision) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 2});
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 7, .rect = {.origin = {}, .columns = 1, .rows = 1}}}, 1));
    const auto before = model.revision();

    const auto result = model.transact({
        .expectedRevision = before,
        .payload =
            SConfigureTopologyCommand{
                .extent           = {.columns = 8, .rows = 2},
                .outputIDs        = {7, 9},
                .topologyRevision = 2,
            },
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(model.revision(), before + 1);
    EXPECT_EQ(model.extent(), (SLuminophoreBoardExtent{.columns = 8, .rows = 2}));
    EXPECT_EQ(model.outputTopologyRevision(), 2);
    ASSERT_EQ(model.outputViews().size(), 2);
    EXPECT_EQ(model.outputViews()[0].outputID, 7);
    EXPECT_EQ(model.outputViews()[1].outputID, 9);
}

TEST(LuminophoreSpatialTransaction, InvalidTopologyKeepsExtentViewsAndRevisionTogether) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 2, .rows = 1});
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 1, .rect = {.origin = {}, .columns = 1, .rows = 1}},
            {.outputID = 2, .rect = {.origin = {.x = 1}, .columns = 1, .rows = 1}},
        },
        1));
    const auto before = model.snapshot();

    const auto result = model.transact({
        .expectedRevision = before.revision,
        .payload =
            SConfigureTopologyCommand{
                .extent           = {.columns = 1, .rows = 1},
                .outputIDs        = {1, 2},
                .topologyRevision = 2,
            },
    });

    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, EmptyOutputDoesNotStealAnotherOutputsFocus) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 5, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 2, .y = 0}));
    ASSERT_TRUE(model.addTiled(3, SLuminophoreBoardPoint{.x = 3, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 10, .rect = {.origin = {}, .columns = 1, .rows = 1}, .anchorKey = 1},
            {.outputID = 20, .rect = {.origin = {.x = 2}, .columns = 2, .rows = 1}, .anchorKey = 3},
        },
        7));

    const auto result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SMoveTiledCommand{.key = 1, .direction = eLuminophoreSpatialDirection::RIGHT},
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_FALSE(result.updatesFocus);
    EXPECT_EQ(result.nextFocusedKey, std::nullopt);
}

TEST(LuminophoreSpatialTransaction, AdjustingOutputViewPublishesItsFocusAnchor) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 1, .rows = 1}}}, 7));

    const auto result = model.transact({
        .expectedRevision = model.revision(),
        .payload =
            SAdjustViewCommand{
                .direction                = eLuminophoreSpatialDirection::RIGHT,
                .anchor                   = {.x = 0, .y = 0},
                .anchorKey                = 1,
                .outputID                 = 10,
                .expectedTopologyRevision = 7,
            },
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    ASSERT_EQ(result.snapshot.outputViews.size(), 1);
    EXPECT_EQ(result.snapshot.outputViews.front().anchorKey, 1);
}

TEST(LuminophoreSpatialTransaction, VisibleTiledUsesTheUnionOfOutputViews) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 5, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 3, .y = 0}));
    ASSERT_TRUE(model.addTiled(3, SLuminophoreBoardPoint{.x = 4, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 10, .rect = {.origin = {}, .columns = 1, .rows = 1}},
            {.outputID = 20, .rect = {.origin = {.x = 3}, .columns = 1, .rows = 1}},
        },
        7));

    EXPECT_EQ(model.visibleTiled(), (std::vector<LuminophoreWindowKey>{1, 2}));
}

TEST(LuminophoreSpatialTransaction, MovingAnAnchorOutsideItsOutputClearsTheStoredAnchor) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 1, .rows = 1}, .anchorKey = 1}}, 7));

    const auto result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SMoveTiledCommand{.key = 1, .direction = eLuminophoreSpatialDirection::RIGHT},
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    ASSERT_EQ(result.snapshot.outputViews.size(), 1);
    EXPECT_EQ(result.snapshot.outputViews.front().anchorKey, std::nullopt);
}

TEST(LuminophoreSpatialTransaction, IdenticalCommandSequenceProducesIdenticalSnapshot) {
    auto applySequence = [] {
        auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 2});
        EXPECT_TRUE(model.addTiled(1));
        EXPECT_TRUE(model.addTiled(2));
        EXPECT_TRUE(model.moveTiled(1, eLuminophoreSpatialDirection::RIGHT) != eLuminophoreSpatialMoveResult::REJECTED);
        EXPECT_TRUE(model.attachFloating(10, {.x = 1, .y = 0}));
        EXPECT_TRUE(model.adjustView(eLuminophoreSpatialDirection::DOWN, {.x = 0, .y = 0}));
        return model.snapshot();
    };

    const auto expected = applySequence();
    for (int iteration = 0; iteration < 100; ++iteration)
        EXPECT_EQ(applySequence(), expected);
}

TEST(LuminophoreSpatialTransaction, ObservedPlacementTransitionsPublishOneRevisionEach) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});

    auto result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SObserveWindowCommand{.key = 7, .mode = eLuminophoreWindowPlacementMode::TILED, .preferred = SLuminophoreBoardPoint{.x = 1, .y = 0}},
    });
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.snapshot.revision, 1);
    EXPECT_EQ(model.coordinateOf(7), (SLuminophoreBoardPoint{.x = 1, .y = 0}));

    result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SObserveWindowCommand{.key = 7, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{.x = 1, .y = 0}},
    });
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.snapshot.revision, 2);
    EXPECT_EQ(model.coordinateOf(7), std::nullopt);
    EXPECT_EQ(model.floatingHostOf(7), (SLuminophoreBoardPoint{.x = 1, .y = 0}));

    result = model.transact({
        .expectedRevision = model.revision(),
        .payload          = SObserveWindowCommand{.key = 7, .mode = eLuminophoreWindowPlacementMode::ABSENT},
    });
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.snapshot.revision, 3);
    EXPECT_EQ(model.floatingHostOf(7), std::nullopt);
    EXPECT_TRUE(model.validate());
}

TEST(LuminophoreSpatialTransaction, DuplicateObservationIsANoOp) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    ASSERT_TRUE(model.addTiled(7, SLuminophoreBoardPoint{.x = 1, .y = 0}));
    const auto before = model.snapshot();

    const auto result = model.transact({
        .expectedRevision = before.revision,
        .payload          = SObserveWindowCommand{.key = 7, .mode = eLuminophoreWindowPlacementMode::TILED, .preferred = SLuminophoreBoardPoint{.x = 2, .y = 0}},
    });

    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, FailedFloatingToTiledTransitionKeepsOriginalPlacement) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 1, .rows = 1});
    ASSERT_TRUE(model.addTiled(1));
    ASSERT_TRUE(model.attachFloating(7, {.x = 0, .y = 0}));
    const auto before = model.snapshot();

    const auto result = model.transact({
        .expectedRevision = before.revision,
        .payload          = SObserveWindowCommand{.key = 7, .mode = eLuminophoreWindowPlacementMode::TILED},
    });

    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::NO_CAPACITY);
    EXPECT_EQ(model.snapshot(), before);
    EXPECT_EQ(model.floatingHostOf(7), (SLuminophoreBoardPoint{.x = 0, .y = 0}));
}

TEST(LuminophoreSpatialTransaction, FloatingGeometryUpdateIsOneAtomicRevision) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    ASSERT_TRUE(model.attachFloating(7, {.x = 0, .y = 0}));
    const auto               before = model.snapshot();
    const SLuminophoreNormalizedBox local  = {.x = 2000, .y = 1500, .width = 6000, .height = 7000};

    const auto               result = model.transact({
        .expectedRevision = before.revision,
        .payload          = SUpdateFloatingCommand{.key = 7, .host = {.x = 1, .y = 0}, .localBox = local},
    });

    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.snapshot.revision, before.revision + 1);
    ASSERT_EQ(result.snapshot.floating.size(), 1);
    EXPECT_EQ(result.snapshot.floating[0], (SLuminophoreFloatingPlacement{.key = 7, .host = {.x = 1, .y = 0}, .localBox = local}));
}

TEST(LuminophoreSpatialTransaction, InvalidFloatingGeometryCannotPartiallyMoveHost) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 2});
    ASSERT_TRUE(model.attachFloating(7, {.x = 0, .y = 0}));
    const auto before = model.snapshot();

    const auto result = model.transact({
        .expectedRevision = before.revision,
        .payload          = SUpdateFloatingCommand{.key = 7, .host = {.x = 1, .y = 0}, .localBox = {.x = 9000, .y = 0, .width = 0, .height = 5000}},
    });

    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, OffViewWindowCanBeMovedRepeatedlyWithoutFocusChanges) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 5, .rows = 1});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 1}));
    for (int x = 2; x <= 4; ++x) {
        const auto result = model.transact({.expectedRevision = model.revision(), .payload = SMoveTiledCommand{.key = 1, .direction = eLuminophoreSpatialDirection::RIGHT}});
        ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
        EXPECT_FALSE(result.updatesFocus);
        EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{.x = x}));
    }
}

TEST(LuminophoreSpatialTransaction, UnmovedFloatingReleaseIsANoOp) {
    auto                     model = CLuminophoreSpatialModel::finiteFixture({.columns = 15, .rows = 5});
    const SLuminophoreNormalizedBox box{.x = -1000, .width = 12000};
    ASSERT_TRUE(model.attachFloating(7, {.x = 2}, box));
    const auto before = model.snapshot();
    const auto result = model.transact({.expectedRevision = model.revision(), .payload = SUpdateFloatingCommand{.key = 7, .host = {.x = 2}, .localBox = box}});
    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, DirectionalFocusChoosesNearestAndRevealsWithOneRevision) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 15, .rows = 5});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 4, .y = 3}));
    ASSERT_TRUE(model.addTiled(3, SLuminophoreBoardPoint{.x = 6, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 2}}}, 7));
    const auto before = model.snapshot();
    const auto result = model.transact({.expectedRevision = model.revision(),
                                        .payload = SFocusDirectionCommand{.key = 1, .direction = eLuminophoreSpatialDirection::RIGHT, .outputID = 10, .expectedTopologyRevision = 7}});
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_TRUE(result.updatesFocus);
    EXPECT_EQ(result.nextFocusedKey, 2);
    EXPECT_EQ(model.revision(), before.revision + 1);
    EXPECT_EQ(model.snapshot().tiled, before.tiled);
    EXPECT_EQ(model.outputViews().front().rect, (SLuminophoreViewRect{.origin = {.x = 3, .y = 2}, .columns = 2, .rows = 2}));
}

TEST(LuminophoreSpatialTransaction, DirectionalFocusVisibleOnOtherOutputDoesNotMoveViews) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 4, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews(
        {{.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 1}}, {.outputID = 20, .rect = {.origin = {.x = 4}, .columns = 2, .rows = 1}}}, 7));
    const auto views  = model.outputViews();
    const auto result = model.transact({.expectedRevision = model.revision(),
                                        .payload = SFocusDirectionCommand{.key = 1, .direction = eLuminophoreSpatialDirection::RIGHT, .outputID = 10, .expectedTopologyRevision = 7}});
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.nextFocusedKey, 2);
    EXPECT_EQ(model.outputViews(), views);
}

TEST(LuminophoreSpatialTransaction, DirectionalFocusRejectsStaleAndEmptyWithoutFocusMutation) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{}));
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 1}}}, 7));
    const auto before  = model.snapshot();
    auto       command = SFocusDirectionCommand{.key = 1, .direction = eLuminophoreSpatialDirection::LEFT, .outputID = 10, .expectedTopologyRevision = 6};
    auto       result  = model.transact({.expectedRevision = model.revision(), .payload = command});
    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY);
    EXPECT_FALSE(result.updatesFocus);
    command.expectedTopologyRevision = 7;
    result                           = model.transact({.expectedRevision = model.revision(), .payload = command});
    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_FALSE(result.updatesFocus);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, DirectionalFocusCollisionFailureDoesNotPublishFocusOrViews) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 7, .y = 1}));
    ASSERT_TRUE(model.configureOutputViews(
        {{.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 2}}, {.outputID = 20, .rect = {.origin = {.x = 6}, .columns = 2, .rows = 1}}}, 7));
    const auto before = model.snapshot();
    const auto result = model.transact({.expectedRevision = model.revision(),
                                        .payload = SFocusDirectionCommand{.key = 1, .direction = eLuminophoreSpatialDirection::RIGHT, .outputID = 10, .expectedTopologyRevision = 7}});
    EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_FALSE(result.updatesFocus);
    EXPECT_EQ(result.nextFocusedKey, std::nullopt);
    EXPECT_EQ(model.snapshot(), before);
}

TEST(LuminophoreSpatialTransaction, DirectionalFocusUsesAllFourHalfPlanesAndStableTies) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 9, .rows = 5});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 4, .y = 2}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 3, .y = 1}));
    ASSERT_TRUE(model.addTiled(3, SLuminophoreBoardPoint{.x = 3, .y = 3}));
    ASSERT_TRUE(model.addTiled(4, SLuminophoreBoardPoint{.x = 5, .y = 1}));
    ASSERT_TRUE(model.addTiled(5, SLuminophoreBoardPoint{.x = 5, .y = 3}));
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 9, .rows = 5}}}, 7));
    const std::vector<std::pair<eLuminophoreSpatialDirection, LuminophoreWindowKey>> cases = {
        {eLuminophoreSpatialDirection::LEFT, 2},
        {eLuminophoreSpatialDirection::RIGHT, 4},
        {eLuminophoreSpatialDirection::UP, 2},
        {eLuminophoreSpatialDirection::DOWN, 3},
    };
    for (const auto& [direction, expected] : cases) {
        const auto result = model.transact(
            {.expectedRevision = model.revision(), .payload = SFocusDirectionCommand{.key = 1, .direction = direction, .outputID = 10, .expectedTopologyRevision = 7}});
        ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
        EXPECT_EQ(result.nextFocusedKey, expected);
    }
}

TEST(LuminophoreSpatialTransaction, DesktopRoundTripPreservesNormalAndWidePlacement) {
    for (const bool wide : {false, true}) {
        auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
        ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{}));
        ASSERT_TRUE(model.attachFloating(2, SLuminophoreBoardPoint{}));
        ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 1}}}, 7));
        if (wide) {
            ASSERT_TRUE(model.enterWide(1));
        }
        const auto before = model.snapshot();
        auto       result = model.transact({.expectedRevision = model.revision(), .payload = SToggleDesktopCommand{}});
        ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
        EXPECT_EQ(result.snapshot.presentationMode, eLuminophorePresentationMode::DESKTOP);
        EXPECT_EQ(result.snapshot.desktopReturnMode, before.presentationMode);
        EXPECT_EQ(result.snapshot.tiled, before.tiled);
        EXPECT_EQ(result.snapshot.floating, before.floating);
        EXPECT_EQ(result.snapshot.outputViews, before.outputViews);
        result = model.transact({.expectedRevision = model.revision(), .payload = SToggleDesktopCommand{}});
        ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
        auto restored = model.snapshot();
        EXPECT_EQ(restored.revision, before.revision + 2);
        restored.revision = before.revision;
        EXPECT_EQ(restored, before);
    }
}

TEST(LuminophoreSpatialTransaction, RemovingWideTargetWhileDesktopExposedRestoresNormal) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{}));
    ASSERT_TRUE(model.enterWide(1));
    ASSERT_EQ(model.transact({.expectedRevision = model.revision(), .payload = SToggleDesktopCommand{}}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    ASSERT_TRUE(model.remove(1));
    EXPECT_EQ(model.snapshot().presentationMode, eLuminophorePresentationMode::DESKTOP);
    EXPECT_EQ(model.snapshot().desktopReturnMode, eLuminophorePresentationMode::NORMAL);
    EXPECT_EQ(model.snapshot().wideKey, std::nullopt);
    ASSERT_EQ(model.transact({.expectedRevision = model.revision(), .payload = SToggleDesktopCommand{}}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(model.snapshot().presentationMode, eLuminophorePresentationMode::NORMAL);
    EXPECT_TRUE(model.validate());
}

static CLuminophoreSpatialModel editorModel() {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 4});
    model.configureOutputViews(
        {
            {.outputID = 10, .rect = {.origin = {}, .columns = 2, .rows = 2}},
            {.outputID = 20, .rect = {.origin = {.x = 4}, .columns = 2, .rows = 2}},
        },
        7);
    return model;
}

TEST(LuminophoreSpatialTransaction, EditorWindowPreviewAndCommitUseSamePushWithoutChangingFocus) {
    auto model = editorModel();
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 4, .y = 0}));
    const auto                before  = model.snapshot();
    const SLuminophoreSpatialCommand command = {.expectedRevision = model.revision(),
                                         .payload          = SMoveWindowToCommand{.key = 1, .point = {.x = 4, .y = 0}, .outputID = 10, .expectedTopologyRevision = 7}};
    const auto                preview = model.preview(command);
    ASSERT_EQ(preview.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(preview.moveResult, eLuminophoreSpatialMoveResult::PUSHED);
    EXPECT_EQ(model.snapshot(), before);
    const auto committed = model.transact(command);
    EXPECT_EQ(committed.snapshot, preview.snapshot);
    EXPECT_EQ(committed.moveResult, preview.moveResult);
    EXPECT_FALSE(committed.updatesFocus);
    EXPECT_EQ(model.revision(), before.revision + 1);
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{.x = 4, .y = 0}));
    EXPECT_EQ(model.coordinateOf(2), (SLuminophoreBoardPoint{.x = 5, .y = 0}));
    EXPECT_EQ(model.transact(command).status, eLuminophoreSpatialTransactionStatus::STALE_REVISION);
}

TEST(LuminophoreSpatialTransaction, EditorFloatingHostMovePreservesExactLocalBox) {
    auto                     model = editorModel();
    const SLuminophoreNormalizedBox box   = {.x = -1000, .y = 300, .width = 14000, .height = 7000};
    ASSERT_TRUE(model.attachFloating(3, {.x = 0, .y = 0}, box));
    const SLuminophoreSpatialCommand command = {.expectedRevision = model.revision(),
                                         .payload          = SMoveWindowToCommand{.key = 3, .point = {.x = 7, .y = 3}, .outputID = 10, .expectedTopologyRevision = 7}};
    const auto                preview = model.preview(command);
    const auto                result  = model.transact(command);
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.snapshot, preview.snapshot);
    ASSERT_EQ(result.snapshot.floating.size(), 1U);
    EXPECT_EQ(result.snapshot.floating[0].localBox, box);
    EXPECT_EQ(result.snapshot.floating[0].host, (SLuminophoreBoardPoint{.x = 7, .y = 3}));
    const auto revision  = model.revision();
    const auto unchanged = model.transact({.expectedRevision = revision, .payload = command.payload});
    EXPECT_EQ(unchanged.status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_EQ(model.revision(), revision);
}

TEST(LuminophoreSpatialTransaction, EditorViewMoveAndResizePreviewMatchCollisionCommit) {
    const std::vector<LuminophoreSpatialPayload> payloads = {
        SMoveOutputViewToCommand{.outputID = 10, .origin = {.x = 3, .y = 0}, .expectedTopologyRevision = 7},
        SResizeOutputViewCommand{.outputID = 10, .rect = {.origin = {}, .columns = 5, .rows = 2}, .expectedTopologyRevision = 7},
    };
    for (const auto& payload : payloads) {
        auto                      model   = editorModel();
        const auto                before  = model.snapshot();
        const SLuminophoreSpatialCommand command = {.expectedRevision = model.revision(), .payload = payload};
        const auto                preview = model.preview(command);
        EXPECT_EQ(model.snapshot(), before);
        const auto result = model.transact(command);
        ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
        EXPECT_EQ(result.snapshot, preview.snapshot);
        EXPECT_EQ(model.outputViews()[1].rect.origin.x, 5);
        EXPECT_EQ(model.revision(), before.revision + 1);
        EXPECT_FALSE(result.updatesFocus);
    }
}

TEST(LuminophoreSpatialTransaction, EditorInvalidAndUnsolvableViewChangesAreAtomic) {
    const std::vector<LuminophoreSpatialPayload> payloads = {
        SMoveOutputViewToCommand{.outputID = 10, .origin = {.x = -1}, .expectedTopologyRevision = 7},
        SMoveOutputViewToCommand{.outputID = 99, .origin = {}, .expectedTopologyRevision = 7},
        SResizeOutputViewCommand{.outputID = 10, .rect = {.origin = {}, .columns = 7, .rows = 2}, .expectedTopologyRevision = 7},
        SResizeOutputViewCommand{.outputID = 10, .rect = {.origin = {}, .columns = 0, .rows = 2}, .expectedTopologyRevision = 7},
    };
    for (const auto& payload : payloads) {
        auto                      model   = editorModel();
        const auto                before  = model.snapshot();
        const SLuminophoreSpatialCommand command = {.expectedRevision = model.revision(), .payload = payload};
        const auto                preview = model.preview(command);
        const auto                result  = model.transact(command);
        EXPECT_EQ(result.status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
        EXPECT_EQ(preview.status, result.status);
        EXPECT_EQ(model.snapshot(), before);
    }
}

TEST(LuminophoreSpatialTransaction, EditorRejectsStaleModelOrTopologyForEveryMutation) {
    const std::vector<LuminophoreSpatialPayload> payloads = {
        SMoveWindowToCommand{.key = 1, .point = {.x = 2}, .outputID = 10, .expectedTopologyRevision = 6},
        SMoveOutputViewToCommand{.outputID = 10, .origin = {.x = 2}, .expectedTopologyRevision = 6},
        SResizeOutputViewCommand{.outputID = 10, .rect = {.origin = {}, .columns = 3, .rows = 2}, .expectedTopologyRevision = 6},
    };
    for (const auto& payload : payloads) {
        auto model = editorModel();
        ASSERT_TRUE(model.addTiled(1));
        const auto before = model.snapshot();
        EXPECT_EQ(model.preview({.expectedRevision = model.revision() - 1, .payload = payload}).status, eLuminophoreSpatialTransactionStatus::STALE_REVISION);
        EXPECT_EQ(model.transact({.expectedRevision = model.revision(), .payload = payload}).status, eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY);
        EXPECT_EQ(model.snapshot(), before);
    }
}

TEST(LuminophoreSpatialTransaction, EditorDirectMoveSwapsOnFullBoard) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 1});
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.origin = {}, .columns = 3, .rows = 1}}}, 7));
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 1}));
    ASSERT_TRUE(model.addTiled(3, SLuminophoreBoardPoint{.x = 2}));
    const auto result =
        model.transact({.expectedRevision = model.revision(), .payload = SMoveWindowToCommand{.key = 1, .point = {.x = 2}, .outputID = 10, .expectedTopologyRevision = 7}});
    EXPECT_EQ(result.moveResult, eLuminophoreSpatialMoveResult::SWAPPED);
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{.x = 2}));
    EXPECT_EQ(model.coordinateOf(3), (SLuminophoreBoardPoint{.x = 0}));
    EXPECT_TRUE(model.validate());
}

TEST(LuminophoreSpatialTransaction, EditorResizeLeftEdgePreservesOppositeEdgeAndAnchor) {
    auto model = editorModel();
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 4, .y = 0}));
    auto views         = model.outputViews();
    views[1].anchorKey = 1;
    ASSERT_TRUE(model.configureOutputViews(views, 7));
    const auto result = model.transact({.expectedRevision = model.revision(),
                                        .payload = SResizeOutputViewCommand{.outputID = 20, .rect = {.origin = {.x = 3}, .columns = 3, .rows = 2}, .expectedTopologyRevision = 7}});
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(model.outputViews()[1].rect.origin.x + model.outputViews()[1].rect.columns, 6);
    EXPECT_EQ(model.outputViews()[1].anchorKey, 1);
    EXPECT_EQ(model.outputViews()[0], views[0]);
}
