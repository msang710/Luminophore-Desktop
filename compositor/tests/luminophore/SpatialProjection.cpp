#include "../../src/luminophore/LuminophoreSpatialProjection.hpp"

#include <algorithm>
#include <gtest/gtest.h>

static const std::vector<SLuminophorePhysicalOutput> DUAL_1080P = {
    {.id = 1, .box = {.x = 0, .y = 0, .width = 1920, .height = 1080}},
    {.id = 2, .box = {.x = 1920, .y = 0, .width = 1920, .height = 1080}},
};

TEST(LuminophoreSpatialProjection, DerivesBoardFromCombinedPhysicalCanvas) {
    EXPECT_EQ(CLuminophoreSpatialProjection::boardExtentFor(DUAL_1080P), (SLuminophoreBoardExtent{.columns = 15, .rows = 5}));
}

TEST(LuminophoreSpatialProjection, DefaultTwoByOneViewMapsOneCellPerMonitor) {
    const auto cells = CLuminophoreSpatialProjection::project({.origin = {}, .columns = 2, .rows = 1}, DUAL_1080P);
    ASSERT_EQ(cells.size(), 2);
    EXPECT_EQ(cells[0], (SLuminophoreProjectedCell{.point = {.x = 0, .y = 0}, .outputID = 1, .box = {.x = 0, .y = 0, .width = 1920, .height = 1080}}));
    EXPECT_EQ(cells[1], (SLuminophoreProjectedCell{.point = {.x = 1, .y = 0}, .outputID = 2, .box = {.x = 1920, .y = 0, .width = 1920, .height = 1080}}));
}

TEST(LuminophoreSpatialProjection, LegacyOneByOneHelperRetainsCompatibilityAllocation) {
    const auto cells = CLuminophoreSpatialProjection::project({.origin = {.x = 4, .y = 2}, .columns = 1, .rows = 1}, DUAL_1080P);
    ASSERT_EQ(cells.size(), 2);
    EXPECT_EQ(cells[0].point, (SLuminophoreBoardPoint{.x = 4, .y = 2}));
    EXPECT_EQ(cells[1].point, (SLuminophoreBoardPoint{.x = 4, .y = 2}));
    EXPECT_EQ(cells[0].outputID, 1);
    EXPECT_EQ(cells[1].outputID, 2);
}

TEST(LuminophoreSpatialProjection, OddColumnCountsNeverCrossThePhysicalSeam) {
    const auto cells = CLuminophoreSpatialProjection::project({.origin = {}, .columns = 3, .rows = 1}, DUAL_1080P);
    ASSERT_EQ(cells.size(), 3);
    EXPECT_EQ(cells[0].outputID, 1);
    EXPECT_EQ(cells[1].outputID, 1);
    EXPECT_EQ(cells[2].outputID, 2);
    EXPECT_EQ(cells[0].box, (SLuminophorePhysicalBox{.x = 0, .y = 0, .width = 960, .height = 1080}));
    EXPECT_EQ(cells[1].box, (SLuminophorePhysicalBox{.x = 960, .y = 0, .width = 960, .height = 1080}));
    EXPECT_EQ(cells[2].box, (SLuminophorePhysicalBox{.x = 1920, .y = 0, .width = 1920, .height = 1080}));
}

TEST(LuminophoreSpatialProjection, RowsAreSplitInsideEachOutput) {
    const auto cells = CLuminophoreSpatialProjection::project({.origin = {}, .columns = 2, .rows = 2}, DUAL_1080P);
    ASSERT_EQ(cells.size(), 4);
    EXPECT_EQ(cells[0].box.height, 540);
    EXPECT_EQ(cells[1].box.y, 540);
    EXPECT_EQ(cells[2].box.height, 540);
    EXPECT_EQ(cells[3].box.y, 540);
}

TEST(LuminophoreSpatialProjection, PlanIsIndependentOfOutputEnumerationOrder) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 32, .rows = 9});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 1, .y = 0}));
    auto reversed = DUAL_1080P;
    std::ranges::reverse(reversed);

    const auto first  = CLuminophoreSpatialProjection::plan(model.snapshot(), 7, DUAL_1080P);
    const auto second = CLuminophoreSpatialProjection::plan(model.snapshot(), 7, reversed);

    ASSERT_TRUE(first.has_value());
    ASSERT_TRUE(second.has_value());
    EXPECT_EQ(*first, *second);
}

TEST(LuminophoreSpatialProjection, ScaleAndTransformParticipateInTopologyIdentity) {
    auto scaled          = DUAL_1080P;
    scaled[0].scaleMilli = 1250;
    auto rotated         = DUAL_1080P;
    rotated[1].transform = 1;

    EXPECT_NE(scaled, DUAL_1080P);
    EXPECT_NE(rotated, DUAL_1080P);
}

TEST(LuminophoreSpatialProjection, PlanKeepsVisibleAndHiddenPlacementsExplicit) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 3, .y = 1}));

    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 3, DUAL_1080P);

    ASSERT_TRUE(plan.has_value());
    ASSERT_EQ(plan->windows.size(), 2);
    EXPECT_TRUE(plan->windows[0].visible);
    EXPECT_EQ(plan->windows[0].fragments.size(), 1);
    EXPECT_FALSE(plan->windows[1].visible);
    EXPECT_TRUE(plan->windows[1].fragments.empty());
}

TEST(LuminophoreSpatialProjection, EmptyCellsRemainExplicitWithoutInventingPlacements) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 2});
    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::DOWN, {.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 1, .y = 0}));
    ASSERT_TRUE(model.addTiled(3, SLuminophoreBoardPoint{.x = 0, .y = 1}));

    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 3, DUAL_1080P);

    ASSERT_TRUE(plan.has_value());
    EXPECT_EQ(plan->cells.size(), 4);
    EXPECT_EQ(plan->windows.size(), 3);
    EXPECT_TRUE(std::ranges::any_of(plan->cells, [](const auto& cell) { return cell.point == SLuminophoreBoardPoint{.x = 1, .y = 1}; }));
    EXPECT_FALSE(std::ranges::any_of(plan->windows, [](const auto& window) { return window.point == SLuminophoreBoardPoint{.x = 1, .y = 1}; }));
}

TEST(LuminophoreSpatialProjection, WideCellProducesOneOutputLocalFragmentPerMonitor) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.enterWide(1));

    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 4, DUAL_1080P);

    ASSERT_TRUE(plan.has_value());
    ASSERT_EQ(plan->windows.size(), 1);
    ASSERT_EQ(plan->windows[0].fragments.size(), 2);
    EXPECT_EQ(plan->windows[0].fragments[0].outputID, 1);
    EXPECT_EQ(plan->windows[0].fragments[1].outputID, 2);
}

TEST(LuminophoreSpatialProjection, ExplicitNormalOneByOneViewNeverSpansOutputs) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 1, .rect = {.origin = {}, .columns = 1, .rows = 1}},
            {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 1}},
        },
        9));

    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 9, DUAL_1080P);

    ASSERT_TRUE(plan);
    ASSERT_EQ(plan->windows.size(), 1);
    ASSERT_EQ(plan->windows[0].fragments.size(), 1);
    EXPECT_EQ(plan->windows[0].fragments[0].outputID, 1);
}

TEST(LuminophoreSpatialProjection, ExplicitNormalProjectsAsymmetricViewsInsideTheirOwnOutputs) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 4});
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 1, .rect = {.origin = {}, .columns = 1, .rows = 1}},
            {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 3}},
        },
        9));

    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 9, DUAL_1080P);

    ASSERT_TRUE(plan);
    ASSERT_EQ(plan->cells.size(), 4);
    EXPECT_EQ(std::ranges::count(plan->cells, 1, &SLuminophoreProjectedCell::outputID), 1);
    EXPECT_EQ(std::ranges::count(plan->cells, 2, &SLuminophoreProjectedCell::outputID), 3);
    EXPECT_TRUE(std::ranges::all_of(plan->cells, [](const auto& cell) {
        return cell.outputID == 1 ? cell.box.x >= 0 && cell.box.x + cell.box.width <= 1920 : cell.box.x >= 1920 && cell.box.x + cell.box.width <= 3840;
    }));
}

TEST(LuminophoreSpatialProjection, ExplicitWideShowsOnlyWideKeyOnEveryOutput) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{.x = 0, .y = 0}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{.x = 2, .y = 0}));
    ASSERT_TRUE(model.attachFloating(3, {.x = 0, .y = 0}));
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 1, .rect = {.origin = {}, .columns = 1, .rows = 1}},
            {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 1}},
        },
        9));
    ASSERT_TRUE(model.enterWide(1));

    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 9, DUAL_1080P);

    ASSERT_TRUE(plan);
    ASSERT_EQ(plan->windows.size(), 3);
    EXPECT_TRUE(plan->windows[0].visible);
    EXPECT_EQ(plan->windows[0].fragments.size(), 2);
    EXPECT_FALSE(plan->windows[1].visible);
    EXPECT_TRUE(plan->windows[1].fragments.empty());
    EXPECT_EQ(plan->windows[2].key, 3U);
    EXPECT_FALSE(plan->windows[2].visible);
    EXPECT_TRUE(plan->windows[2].fragments.empty());
}

TEST(LuminophoreSpatialProjection, ExplicitNormalRejectsStaleOrMismatchedOutputTopology) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 8, .rows = 2});
    ASSERT_TRUE(model.configureOutputViews(
        {
            {.outputID = 1, .rect = {.origin = {}, .columns = 1, .rows = 1}},
            {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 1}},
        },
        9));

    EXPECT_FALSE(CLuminophoreSpatialProjection::plan(model.snapshot(), 8, DUAL_1080P));
    EXPECT_FALSE(CLuminophoreSpatialProjection::plan(model.snapshot(), 9, {DUAL_1080P[0]}));
}

TEST(LuminophoreSpatialProjection, RejectsInvalidOrDuplicateOutputTopology) {
    auto model           = CLuminophoreSpatialModel::finiteFixture({.columns = 4, .rows = 2});
    auto duplicate       = DUAL_1080P;
    duplicate[1].id      = duplicate[0].id;
    auto invalid         = DUAL_1080P;
    invalid[0].box.width = 0;

    EXPECT_FALSE(CLuminophoreSpatialProjection::plan(model.snapshot(), 1, duplicate).has_value());
    EXPECT_FALSE(CLuminophoreSpatialProjection::plan(model.snapshot(), 1, invalid).has_value());
}

TEST(LuminophoreSpatialProjection, NormalizedFloatingBoxRoundTripsWithinOnePixel) {
    const SLuminophorePhysicalBox host = {.x = 1920, .y = 0, .width = 1920, .height = 1080};
    const SLuminophorePhysicalBox box  = {.x = 2111, .y = 107, .width = 901, .height = 507};

    const auto             normalized = CLuminophoreSpatialProjection::normalize(box, host);
    ASSERT_TRUE(normalized.has_value());
    const auto projected = CLuminophoreSpatialProjection::denormalize(*normalized, host);
    ASSERT_TRUE(projected.has_value());
    EXPECT_LE(std::abs(projected->x - box.x), 1);
    EXPECT_LE(std::abs(projected->y - box.y), 1);
    EXPECT_LE(std::abs(projected->width - box.width), 1);
    EXPECT_LE(std::abs(projected->height - box.height), 1);
}

TEST(LuminophoreSpatialProjection, FloatingWindowRetainsLocalBoxAcrossViewResize) {
    auto                     model = CLuminophoreSpatialModel::finiteFixture({.columns = 32, .rows = 9});
    const SLuminophoreNormalizedBox local = {.x = 1250, .y = 2000, .width = 5000, .height = 6000};
    ASSERT_TRUE(model.attachFloating(9, {.x = 0, .y = 0}, local));

    const auto oneRow = CLuminophoreSpatialProjection::plan(model.snapshot(), 1, DUAL_1080P);
    ASSERT_TRUE(oneRow.has_value());
    ASSERT_EQ(oneRow->windows.size(), 1);
    ASSERT_TRUE(oneRow->windows[0].floating);
    const auto first = oneRow->windows[0].fragments[0].box;

    ASSERT_TRUE(model.adjustView(eLuminophoreSpatialDirection::DOWN, {.x = 0, .y = 0}));
    const auto twoRows = CLuminophoreSpatialProjection::plan(model.snapshot(), 1, DUAL_1080P);
    ASSERT_TRUE(twoRows.has_value());
    ASSERT_EQ(twoRows->windows.size(), 1);
    EXPECT_EQ(twoRows->windows[0].fragments[0].box.height, first.height / 2);
    EXPECT_EQ(model.snapshot().floating[0].localBox, local);
}

TEST(LuminophoreSpatialProjection, FloatingBoxCrossingCellBoundaryDoesNotShrink) {
    const SLuminophorePhysicalBox host{.x = 1920, .y = 0, .width = 1920, .height = 1080};
    const SLuminophorePhysicalBox box{.x = 1700, .y = -20, .width = 900, .height = 1200};
    const auto             normalized = CLuminophoreSpatialProjection::normalize(box, host);
    ASSERT_TRUE(normalized);
    EXPECT_LT(normalized->x, 0);
    EXPECT_GT(normalized->height, SLuminophoreNormalizedBox::BASIS);
    const auto result = CLuminophoreSpatialProjection::denormalize(*normalized, host);
    ASSERT_TRUE(result);
    EXPECT_LE(std::abs(result->x - box.x), 1);
    EXPECT_LE(std::abs(result->y - box.y), 1);
    EXPECT_LE(std::abs(result->width - box.width), 1);
    EXPECT_LE(std::abs(result->height - box.height), 1);
}

TEST(LuminophoreSpatialProjection, FloatingCrossOutputFragmentsRetainFullClientGeometry) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 15, .rows = 5});
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 1, .rect = {.columns = 1, .rows = 1}}, {.outputID = 2, .rect = {.origin = {.x = 2}, .columns = 1, .rows = 3}}}, 1));
    const auto local = CLuminophoreSpatialProjection::normalize({.x = 1700, .y = -20, .width = 900, .height = 500}, {.x = 1920, .y = 0, .width = 1920, .height = 360});
    ASSERT_TRUE(local);
    ASSERT_TRUE(model.attachFloating(7, {.x = 2}, *local));
    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 1, DUAL_1080P);
    ASSERT_TRUE(plan);
    ASSERT_EQ(plan->windows.size(), 1);
    const auto& window = plan->windows.front();
    ASSERT_TRUE(window.clientBox);
    EXPECT_EQ(window.fragments.size(), 2);
    EXPECT_EQ(window.primaryOutputID, 2);
    EXPECT_LE(std::abs(window.clientBox->width - 900), 1);
    EXPECT_LT(window.clientBox->y, window.fragments.front().box.y);
}

TEST(LuminophoreSpatialProjection, DesktopAndWideKeepExplicitHiddenFloatingEntries) {
    SLuminophoreSpatialSnapshot snapshot;
    snapshot.extent                                = {.columns = 2, .rows = 1};
    snapshot.view                                  = {.origin = {}, .columns = 2, .rows = 1};
    snapshot.tiled                                 = {{.key = 1, .point = {}}};
    snapshot.floating                              = {{.key = 2, .host = {}}};
    const std::vector<SLuminophorePhysicalOutput> outputs = {{.id = 10, .box = {.width = 1920, .height = 1080}}};
    for (const auto mode : {eLuminophorePresentationMode::DESKTOP, eLuminophorePresentationMode::WIDE}) {
        snapshot.presentationMode = mode;
        snapshot.wideKey          = 1;
        const auto plan           = CLuminophoreSpatialProjection::plan(snapshot, 0, outputs);
        ASSERT_TRUE(plan);
        ASSERT_EQ(plan->windows.size(), 2U);
        EXPECT_EQ(plan->presentationMode, mode);
        EXPECT_EQ(plan->windows[0].visible, mode == eLuminophorePresentationMode::WIDE);
        EXPECT_FALSE(plan->windows[1].visible);
        EXPECT_TRUE(plan->windows[1].floating);
        EXPECT_TRUE(plan->windows[1].fragments.empty());
    }
}

TEST(LuminophoreSpatialProjection, FloatingClientSizeSurvivesViewRescaling) {
    const auto local = CLuminophoreSpatialProjection::normalize({.x = 80, .y = 40, .width = 706, .height = 830}, {.x = 0, .y = 0, .width = 640, .height = 360});
    ASSERT_TRUE(local);
    for (const auto host : {SLuminophorePhysicalBox{.x = 0, .y = 0, .width = 1920, .height = 1080}, SLuminophorePhysicalBox{.x = -1920, .y = 0, .width = 320, .height = 180}}) {
        const auto projected = CLuminophoreSpatialProjection::denormalize(*local, host);
        ASSERT_TRUE(projected);
        EXPECT_EQ(projected->width, 706);
        EXPECT_EQ(projected->height, 830);
    }
}
