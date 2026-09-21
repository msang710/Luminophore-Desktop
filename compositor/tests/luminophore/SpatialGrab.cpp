#include "../../src/luminophore/LuminophoreSpatialGrab.hpp"

#include <gtest/gtest.h>
#include <limits>

static SLuminophoreSpatialSnapshot grabSnapshot() {
    return {.extent                 = {.columns = 15, .rows = 5},
            .tiled                  = {{.key = 1, .point = {1, 1}}},
            .floating               = {{.key = 2, .host = {1, 1}}},
            .outputViews            = {{.outputID = 10, .rect = {.columns = 3, .rows = 2}}},
            .outputTopologyRevision = 4,
            .revision               = 7};
}
static SLuminophoreEditorFrame grabFrame() {
    return {.generation = "frame-a", .revision = 5, .outputID = 10, .x = -500.5, .y = 100.25, .width = 200, .height = 100};
}
static std::vector<SLuminophoreEditorCell> grabCells() {
    return {{.point = {1, 1}, .x = 10, .y = 20, .width = 40, .height = 40}, {.point = {2, 1}, .x = 51, .y = 20, .width = 40, .height = 40}};
}

TEST(LuminophoreSpatialGrab, MapsCommittedFrameCoordinatesAndConsumesReleaseOnce) {
    CLuminophoreSpatialGrab grab;
    const auto       snapshot = grabSnapshot();
    const auto       session  = grab.begin(snapshot, 1, 10);
    ASSERT_TRUE(session);
    const auto frame = grabFrame();
    ASSERT_TRUE(grab.bindLayout(session->generation, snapshot, frame, grabCells()));
    const auto preview = grab.commandAt(snapshot, frame, frame.x + 60, frame.y + 30);
    ASSERT_TRUE(preview);
    EXPECT_EQ(preview->expectedRevision, 7U);
    const auto& payload = std::get<SMoveWindowToCommand>(preview->payload);
    EXPECT_EQ(payload.point, (SLuminophoreBoardPoint{2, 1}));
    EXPECT_EQ(payload.expectedTopologyRevision, 4U);
    EXPECT_EQ(payload.key, 1U);
    EXPECT_EQ(payload.outputID, 10U);
    const auto drop = grab.release(snapshot, frame, frame.x + 60, frame.y + 30);
    ASSERT_TRUE(drop);
    EXPECT_EQ(std::get<SMoveWindowToCommand>(drop->payload).point, payload.point);
    EXPECT_FALSE(grab.release(snapshot, frame, frame.x + 60, frame.y + 30));
    EXPECT_FALSE(grab.bindLayout(session->generation, snapshot, frame, grabCells()));
}

TEST(LuminophoreSpatialGrab, RejectsStaleModelTopologyAndPresentationFrame) {
    const auto original = grabSnapshot();
    for (int kind = 0; kind < 6; ++kind) {
        CLuminophoreSpatialGrab grab;
        const auto       session = grab.begin(original, 1, 10);
        ASSERT_TRUE(session);
        auto frame = grabFrame();
        ASSERT_TRUE(grab.bindLayout(session->generation, original, frame, grabCells()));
        auto snapshot = original;
        if (kind == 0)
            ++snapshot.revision;
        else if (kind == 1)
            ++snapshot.outputTopologyRevision;
        else if (kind == 2)
            ++frame.revision;
        else if (kind == 3)
            frame.generation = "replacement";
        else if (kind == 4)
            frame.x += 1;
        else
            snapshot.tiled.clear();
        EXPECT_FALSE(grab.release(snapshot, frame, frame.x + 20, frame.y + 30));
        EXPECT_FALSE(grab.state());
    }
}

TEST(LuminophoreSpatialGrab, InvalidReplacementClearsPreviousGeometryButOldGrabCannotClearNewOne) {
    CLuminophoreSpatialGrab grab;
    const auto       snapshot = grabSnapshot();
    const auto       first    = grab.begin(snapshot, 1, 10);
    const auto       second   = grab.begin(snapshot, 1, 10);
    ASSERT_TRUE(first && second);
    EXPECT_GT(second->generation, first->generation);
    ASSERT_TRUE(grab.bindLayout(second->generation, snapshot, grabFrame(), grabCells()));
    EXPECT_FALSE(grab.bindLayout(first->generation, snapshot, grabFrame(), {}));
    EXPECT_TRUE(grab.commandAt(snapshot, grabFrame(), -480, 130));
    EXPECT_FALSE(grab.bindLayout(second->generation, snapshot, grabFrame(), {}));
    EXPECT_FALSE(grab.commandAt(snapshot, grabFrame(), -480, 130));
}

TEST(LuminophoreSpatialGrab, RejectsAmbiguousOutOfBoundsAndNonFiniteCells) {
    for (int kind = 0; kind < 6; ++kind) {
        CLuminophoreSpatialGrab grab;
        const auto       snapshot = grabSnapshot();
        const auto       session  = grab.begin(snapshot, 1, 10);
        ASSERT_TRUE(session);
        auto cells = grabCells();
        if (kind == 0)
            cells[1].point = cells[0].point;
        else if (kind == 1)
            cells[1].x = cells[0].x;
        else if (kind == 2)
            cells[1].point.x = 15;
        else if (kind == 3)
            cells[1].width = std::numeric_limits<double>::infinity();
        else if (kind == 4)
            cells[1].width = 300;
        else
            cells[1].height = 0;
        EXPECT_FALSE(grab.bindLayout(session->generation, snapshot, grabFrame(), cells));
        EXPECT_FALSE(grab.release(snapshot, grabFrame(), -480, 130));
    }
}

TEST(LuminophoreSpatialGrab, OutsideGapAndCancellationNeverProduceDrop) {
    for (const double x : {-1000., -450., 1000., std::numeric_limits<double>::quiet_NaN()}) {
        CLuminophoreSpatialGrab grab;
        const auto       snapshot = grabSnapshot();
        const auto       session  = grab.begin(snapshot, 1, 10);
        ASSERT_TRUE(session);
        ASSERT_TRUE(grab.bindLayout(session->generation, snapshot, grabFrame(), grabCells()));
        EXPECT_FALSE(grab.release(snapshot, grabFrame(), x, 130));
    }
    CLuminophoreSpatialGrab grab;
    const auto       session = grab.begin(grabSnapshot(), 1, 10);
    ASSERT_TRUE(session);
    ASSERT_TRUE(grab.bindLayout(session->generation, grabSnapshot(), grabFrame(), grabCells()));
    grab.cancel();
    EXPECT_FALSE(grab.release(grabSnapshot(), grabFrame(), -480, 130));
}

TEST(LuminophoreSpatialGrab, FloatingGrabResolvesGridHostAndInvalidBeginIsRejected) {
    CLuminophoreSpatialGrab grab;
    const auto       snapshot = grabSnapshot();
    const auto       session  = grab.begin(snapshot, 2, 10);
    ASSERT_TRUE(session);
    EXPECT_TRUE(session->floating);
    ASSERT_TRUE(grab.bindLayout(session->generation, snapshot, grabFrame(), grabCells()));
    EXPECT_TRUE(grab.release(snapshot, grabFrame(), -480, 130));
    EXPECT_FALSE(grab.begin(snapshot, 99, 10));
    EXPECT_FALSE(grab.begin(snapshot, 1, 99));
    auto wide             = snapshot;
    wide.presentationMode = eLuminophorePresentationMode::WIDE;
    EXPECT_TRUE(grab.begin(wide, 1, 10));
    wide.presentationMode = eLuminophorePresentationMode::DESKTOP;
    EXPECT_FALSE(grab.begin(wide, 1, 10));
}

TEST(LuminophoreSpatialGrab, PreviewAndReleaseUseIdenticalModelCollisionTransaction) {
    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = 15, .rows = 5});
    ASSERT_TRUE(model.configureOutputViews({{.outputID = 10, .rect = {.columns = 3, .rows = 2}}}, 4));
    ASSERT_TRUE(model.addTiled(1, SLuminophoreBoardPoint{1, 1}));
    ASSERT_TRUE(model.addTiled(2, SLuminophoreBoardPoint{2, 1}));
    const auto       before = model.snapshot();
    CLuminophoreSpatialGrab grab;
    const auto       session = grab.begin(before, 1, 10);
    ASSERT_TRUE(session);
    ASSERT_TRUE(grab.bindLayout(session->generation, before, grabFrame(), grabCells()));
    const auto previewCommand = grab.commandAt(before, grabFrame(), -440, 130);
    ASSERT_TRUE(previewCommand);
    const auto preview = model.preview(*previewCommand);
    ASSERT_EQ(preview.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(model.snapshot(), before);
    const auto commitCommand = grab.release(before, grabFrame(), -440, 130);
    ASSERT_TRUE(commitCommand);
    const auto commit = model.transact(*commitCommand);
    EXPECT_EQ(commit.snapshot, preview.snapshot);
    EXPECT_EQ(commit.moveResult, preview.moveResult);
    EXPECT_FALSE(commit.updatesFocus);
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{2, 1}));
    EXPECT_NE(model.coordinateOf(2), (SLuminophoreBoardPoint{2, 1}));
    EXPECT_EQ(model.revision(), before.revision + 1);
}

TEST(LuminophoreSpatialGrab, SameNativeGrabCanTargetAnotherBoardAndRejectUnknownOutput) {
    auto snapshot = grabSnapshot();
    snapshot.outputViews.push_back({.outputID = 20, .rect = {.columns = 3, .rows = 2}});
    CLuminophoreSpatialGrab grab;
    const auto       session = grab.begin(snapshot, 1, 10);
    ASSERT_TRUE(session);
    auto cells = grabCells();
    for (auto& cell : cells)
        cell.outputID = 20;
    ASSERT_TRUE(grab.bindLayout(session->generation, snapshot, grabFrame(), cells));
    const auto command = grab.commandAt(snapshot, grabFrame(), -440, 130);
    ASSERT_TRUE(command);
    EXPECT_EQ(std::get<SMoveWindowToCommand>(command->payload).outputID, 20U);
    cells[0].outputID = 99;
    EXPECT_FALSE(grab.bindLayout(session->generation, snapshot, grabFrame(), cells));
    EXPECT_FALSE(grab.commandAt(snapshot, grabFrame(), -440, 130));
}

TEST(LuminophoreSpatialGrab, CrossingKeepsGenerationAndRejectsLateTargetLayouts) {
    CLuminophoreSpatialGrab grab;
    auto             snapshot = grabSnapshot();
    snapshot.outputViews.push_back({.outputID = 20, .rect = {.columns = 3, .rows = 2}});
    const auto session = grab.begin(snapshot, 1, 10);
    auto       a       = grabFrame();
    ASSERT_TRUE(grab.bindLayout(session->generation, snapshot, a, grabCells()));
    ASSERT_TRUE(grab.selectTarget(20));
    EXPECT_EQ(grab.state()->generation, session->generation);
    EXPECT_EQ(grab.state()->outputID, 10U);
    EXPECT_FALSE(grab.commandAt(snapshot, a, a.x + 60, a.y + 30));
    auto b        = a;
    b.outputID    = 20;
    b.targetEpoch = 1;
    ASSERT_TRUE(grab.bindLayout(session->generation, snapshot, b, grabCells()));
    EXPECT_FALSE(grab.bindLayout(session->generation, snapshot, a, {}));
    ASSERT_TRUE(grab.commandAt(snapshot, b, b.x + 60, b.y + 30));
    ASSERT_TRUE(grab.selectTarget(10));
    a.targetEpoch = 2;
    ASSERT_TRUE(grab.bindLayout(session->generation, snapshot, a, grabCells()));
    EXPECT_FALSE(grab.bindLayout(session->generation, snapshot, b, {}));
    ASSERT_TRUE(grab.release(snapshot, a, a.x + 60, a.y + 30));
    EXPECT_FALSE(grab.release(snapshot, b, b.x + 60, b.y + 30));
}
TEST(LuminophoreSpatialGrab, PhysicalGapDisablesDropWithoutEndingGrab) {
    CLuminophoreSpatialGrab grab;
    const auto       snapshot = grabSnapshot();
    const auto       session  = grab.begin(snapshot, 1, 10);
    ASSERT_TRUE(grab.selectTarget(0));
    EXPECT_TRUE(grab.state());
    auto frame        = grabFrame();
    frame.targetEpoch = 1;
    EXPECT_FALSE(grab.bindLayout(session->generation, snapshot, frame, grabCells()));
    EXPECT_FALSE(grab.release(snapshot, frame, frame.x + 60, frame.y + 30));
    EXPECT_FALSE(grab.state());
}

TEST(LuminophoreSpatialGrab, DirectDropKeepsSourceAcrossOutputsAndInvalidatesOnExternalChange) {
    auto snapshot = grabSnapshot();
    snapshot.outputViews.push_back({.outputID = 20, .rect = {.columns = 3, .rows = 2}});
    const auto       original = snapshot;
    CLuminophoreSpatialGrab grab;
    ASSERT_TRUE(grab.begin(snapshot, 1, 10));
    for (int i = 0; i < 1000; ++i) {
        grab.selectTarget(i % 2 ? 20 : 10);
        ASSERT_TRUE(grab.commandAtPoint(snapshot, i % 2 ? 20 : 10, {2, 1}));
    }
    EXPECT_EQ(snapshot, original);
    ASSERT_TRUE(grab.selectTarget(0));
    EXPECT_FALSE(grab.commandAtPoint(snapshot, 20, {2, 1}));
    ASSERT_TRUE(grab.selectTarget(20));
    const auto command = grab.commandAtPoint(snapshot, 20, {2, 1});
    ASSERT_TRUE(command);
    EXPECT_EQ(std::get<SMoveWindowToCommand>(command->payload).point, (SLuminophoreBoardPoint{2, 1}));
    ++snapshot.revision;
    EXPECT_FALSE(grab.commandAtPoint(snapshot, 20, {2, 1}));
    snapshot = original;
    ++snapshot.outputTopologyRevision;
    EXPECT_FALSE(grab.commandAtPoint(snapshot, 20, {2, 1}));
    grab.cancel();
    EXPECT_FALSE(grab.commandAtPoint(original, 20, {2, 1}));
}
