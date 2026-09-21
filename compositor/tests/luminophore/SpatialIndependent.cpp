#include <gtest/gtest.h>
#include "../../src/luminophore/LuminophoreSpatialEdit.hpp"
#include <algorithm>
#include "../../src/luminophore/LuminophoreSpatialTransaction.hpp"
#include "../../src/luminophore/LuminophoreSpatialProjection.hpp"
#include "../../src/luminophore/LuminophoreSpatialCommitter.hpp"

static SLuminophoreSpatialTransactionResult send(CLuminophoreSpatialModel& m, LuminophoreSpatialPayload p) {
    return m.transact({m.revision(), std::move(p)});
}
static CLuminophoreSpatialModel independent() {
    CLuminophoreSpatialModel m;
    EXPECT_TRUE(m.independentBoards());

    EXPECT_EQ(send(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {20, "DP-2", 1000, 0, 1000, 800}}, 1}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    return m;
}
TEST(LuminophoreSpatialIndependent, BoardsMayShareCoordinatesAndFocusIsPreserved) {
    auto m = independent();
    ASSERT_EQ(send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    send(m, SSpatialFocusCommand{1, 10});
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    EXPECT_EQ(m.coordinateOf(1), m.coordinateOf(2));
    EXPECT_NE(m.boardFor(1), m.boardFor(2));
    EXPECT_EQ(m.snapshot().independent->state.focus, 1);
    auto plan = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}});
    ASSERT_TRUE(plan);
    auto commit = CLuminophoreSpatialCommitter::prepare(*plan);
    ASSERT_TRUE(commit);
    EXPECT_EQ(commit->entries[0].primaryOutputID, 10);
    EXPECT_EQ(commit->entries[1].primaryOutputID, 20);
}
TEST(LuminophoreSpatialIndependent, CrossBoardDropSwapsAndUnplugReconnectRestores) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    EXPECT_EQ(send(m, SMoveWindowToCommand{1, {0, 0}, 20, 1}).moveResult, eLuminophoreSpatialMoveResult::SWAPPED);
    EXPECT_EQ(m.outputFor(1), 20);
    EXPECT_EQ(m.outputFor(2), 10);
    EXPECT_EQ(send(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}}, 2}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(m.outputFor(1), 10);
    EXPECT_EQ(send(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {30, "DP-2", 1000, 0, 1000, 800}}, 3}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(m.outputFor(1), 30);
    EXPECT_EQ(m.coordinateOf(1), (SLuminophoreBoardPoint{0, 0}));
}
TEST(LuminophoreSpatialIndependent, NegativeViewAndNoOutputAreValidTransactions) {
    auto m = independent();
    EXPECT_EQ(send(m, SMoveOutputViewToCommand{10, {-9, -6}, 1}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(send(m, SSpatialTopologyCommand{{}, 2}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_TRUE(CLuminophoreSpatialProjection::plan(m.snapshot(), 2, {}));
}
TEST(LuminophoreSpatialIndependent, RectangularRegionsAndWideEligibility) {
    auto m = independent();
    send(m, SAddTiledCommand{1});
    send(m, SAddTiledCommand{2});
    send(m, SMoveWindowToCommand{2, {1, 1}, 10, 1});
    const std::vector<SLuminophorePhysicalOutput> outputs{{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}};
    auto                                          plan = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(plan);
    ASSERT_TRUE(CLuminophoreSpatialCommitter::prepare(*plan));
    EXPECT_EQ(plan->windows.front().fragments.size(), 1);
    send(m, SEnterWideCommand{1});
    plan = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(plan);
    EXPECT_EQ(plan->windows.front().fragments.size(), 2);
    auto separated     = outputs;
    separated[1].box.x = 1001;
    plan               = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, separated);
    ASSERT_TRUE(plan);
    EXPECT_EQ(plan->windows.front().fragments.size(), 1);
}
TEST(LuminophoreSpatialIndependent, ResizeSurvivesFocusAndDefaultsDoNotResetUserView) {
    auto m = independent();
    send(m, SAddTiledCommand{1});
    send(m, SAddTiledCommand{2});
    const auto  id   = *m.boardFor(1);
    const auto  s    = m.snapshot();
    const auto& mesh = s.independent->meshes.at(id);
    const auto  face = std::ranges::find_if(mesh.faces, [](const auto& f) { return f.provenance.origin == Luminophore::Spatial::SPoint{0, 0}; });
    ASSERT_NE(face, mesh.faces.end());
    EXPECT_EQ(send(m, SSpatialResizeCommand{1, 1, 100, face->id}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    const auto resized = m.snapshot().independent->meshes.at(id).faces;
    send(m, SSpatialFocusCommand{2, 10});
    EXPECT_EQ(m.snapshot().independent->meshes.at(id).faces, resized);
    send(m, SMoveOutputViewToCommand{10, {-2, -3}, 1});
    send(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {20, "DP-2", 1000, 0, 1000, 800}}, 1, 4, 3});
    EXPECT_EQ(m.snapshot().independent->state.boards.at(id).view.origin, (Luminophore::Spatial::SPoint{-2, -3}));
    EXPECT_EQ(m.snapshot().independent->state.boards.at(id).defaultColumns, 4);
}
TEST(LuminophoreSpatialIndependent, WideNewWindowPreservesViewAndDesktopReturnsWide) {
    auto m = independent();
    send(m, SAddTiledCommand{1});
    send(m, SEnterWideCommand{1});
    const auto view = m.view();
    for (uint64_t key = 2; key < 8; ++key)
        send(m, SObserveWindowCommand{.key = key, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    EXPECT_EQ(m.view(), view);
    send(m, SToggleDesktopCommand{});
    send(m, SToggleDesktopCommand{});
    EXPECT_TRUE(m.isWideKey(1));
    send(m, SRemoveWindowCommand{1});
    EXPECT_FALSE(m.isWideKey(1));
    EXPECT_EQ(m.view(), view);
}
TEST(LuminophoreSpatialIndependent, StaleTopologyAndDuplicateConnectorAreAtomic) {
    auto       m      = independent();
    const auto before = m.snapshot();
    EXPECT_EQ(send(m, SMoveOutputViewToCommand{10, {2, 2}, 0}).status, eLuminophoreSpatialTransactionStatus::STALE_TOPOLOGY);
    EXPECT_EQ(m.snapshot(), before);
    EXPECT_EQ(send(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {20, "DP-1", 1000, 0, 1000, 800}}, 2}).status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    EXPECT_EQ(m.snapshot(), before);
}

TEST(LuminophoreSpatialIndependent, ReclassificationIsIdempotentAndClosedLeaseNeverReappears) {
    auto model = independent();
    send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(model, SSpatialFocusCommand{1, 20});
    EXPECT_EQ(send(model, SSpatialFocusCommand{1, 20}).status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    const auto before = model.snapshot();
    EXPECT_EQ(send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10}).status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_EQ(model.snapshot(), before);
    EXPECT_EQ(send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{0, 0}, .outputID = 20}).status,
              eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_FALSE(model.coordinateOf(1));
    EXPECT_TRUE(model.floatingHostOf(1));
    send(model, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}}, 2});
    EXPECT_EQ(model.outputFor(1), 10);
    send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::ABSENT, .closed = true});
    send(model, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {30, "DP-2", 1000, 0, 1000, 800}}, 3});
    EXPECT_FALSE(model.boardFor(1));
    EXPECT_TRUE(model.snapshot().independent->displaced.empty());
}

TEST(LuminophoreSpatialIndependent, ExplicitMoveCancelsReconnectLease) {
    auto model = independent();
    send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(model, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}}, 2});
    send(model, SMoveWindowToCommand{1, {1, 1}, 10, 2});
    send(model, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {30, "DP-2", 1000, 0, 1000, 800}}, 3});
    EXPECT_EQ(model.outputFor(1), 10);
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{1, 1}));
}

TEST(LuminophoreSpatialIndependent, WideUsesFullLogicalGeometryAcrossScaleAndExcludesPointAdjacency) {
    CLuminophoreSpatialModel model;

    send(model, SSpatialTopologyCommand{{{10, "one", 0, 20, 1000, 780}, {20, "two", 1000, 0, 1000, 800}, {30, "three", 2000, 800, 1000, 800}}, 1});
    send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(model, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 30});
    send(model, SEnterWideCommand{1});
    const auto plan = CLuminophoreSpatialProjection::plan(model.snapshot(), 1,
                                                          {{.id = 10, .name = "one", .box = {0, 20, 1000, 780}, .scaleMilli = 1000, .logicalBox = {0, 0, 1000, 800}},
                                                           {.id = 20, .name = "two", .box = {1000, 0, 1000, 800}, .scaleMilli = 2000, .logicalBox = {1000, 0, 1000, 800}},
                                                           {.id = 30, .name = "three", .box = {2000, 800, 1000, 800}, .scaleMilli = 1000, .logicalBox = {2000, 800, 1000, 800}}});
    ASSERT_TRUE(plan);
    const auto commit = CLuminophoreSpatialCommitter::prepare(*plan);
    ASSERT_TRUE(commit);
    EXPECT_EQ(commit->entries[0].clientBox, (SLuminophorePhysicalBox{0, 0, 2000, 800}));
    EXPECT_EQ(commit->entries[0].fragments.size(), 2U);
    EXPECT_TRUE(commit->entries[1].visible);
    EXPECT_EQ(commit->entries[1].primaryOutputID, 30);
}

TEST(LuminophoreSpatialIndependent, RepeatedDirectMovesSwapWithoutCreatingOrExpandingViews) {
    auto model = independent();
    send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(model, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    const auto before = model.snapshot();
    for (int i = 0; i < 100; ++i) {
        const auto point = model.coordinateOf(2);
        ASSERT_TRUE(point);
        const auto output = model.outputFor(2);
        ASSERT_TRUE(output);
        const auto result = send(model, SMoveWindowToCommand{.key = 1, .point = *point, .outputID = *output, .expectedTopologyRevision = 1});
        ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
        EXPECT_EQ(model.snapshot().tiled.size(), 2U);
        EXPECT_TRUE(model.snapshot().floating.empty());
        const auto views = model.snapshot().outputViews;
        ASSERT_EQ(views.size(), before.outputViews.size());
        for (size_t v = 0; v < views.size(); ++v) {
            EXPECT_EQ(views[v].outputID, before.outputViews[v].outputID);
            EXPECT_EQ(views[v].rect, before.outputViews[v].rect);
        }
    }
}

TEST(LuminophoreSpatialIndependent, FocusTransferRespectsClientMinimumWithoutResettingSizes) {
    auto model = independent();
    send(model, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(model, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(model, SMoveWindowToCommand{.key = 1, .point = {0, 1}, .outputID = 10, .expectedTopologyRevision = 1});
    send(model, SMoveWindowToCommand{.key = 2, .point = {1, 0}, .outputID = 10, .expectedTopologyRevision = 1});
    send(model, SSpatialFocusCommand{1, 10});
    const auto board = *model.boardFor(1);
    const auto old   = model.snapshot().independent->meshes.at(board);
    ASSERT_EQ(send(model, SSpatialFocusCommand{2, 10, {{1, {800, 100}}}}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    auto mesh = model.snapshot().independent->meshes.at(board);
    EXPECT_EQ(mesh.faces, old.faces);
    EXPECT_EQ(mesh.fillFocus, old.fillFocus);
    ASSERT_EQ(model.snapshot().independent->state.focus, 2);
    send(model, SSpatialFocusCommand{2, 10, {{1, {100, 100}}}});
    mesh = model.snapshot().independent->meshes.at(board);
    EXPECT_EQ(mesh.fillFocus, 2);
}

TEST(LuminophoreSpatialIndependent, RectangularCornerResizeCommitsBothAxesOrNeither) {
    auto model = independent();
    for (uint64_t key = 1; key <= 4; ++key)
        send(model, SObserveWindowCommand{.key = key, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    for (uint64_t key = 1; key <= 4; ++key)
        send(model, SMoveWindowToCommand{.key = key, .point = {int64_t((key - 1) % 2), int64_t((key - 1) / 2)}, .outputID = 10, .expectedTopologyRevision = 1});
    send(model, SResizeOutputViewCommand{10, {{0, 0}, 2, 2}, 1});
    const auto  before = model.snapshot();
    const auto& mesh   = before.independent->meshes.at(*model.boardFor(1));
    const auto  face   = std::ranges::find_if(mesh.faces, [](const auto& f) { return f.provenance.origin == Luminophore::Spatial::SPoint{0, 0}; });
    ASSERT_NE(face, mesh.faces.end());
    SSpatialResizeCommand command{1,   static_cast<int>(Luminophore::Spatial::eSide::RIGHT),
                                  40,  face->id,
                                  100, Luminophore::Spatial::SResizeRequest{mesh.revision, UINT64_MAX, Luminophore::Spatial::eSide::BOTTOM, 30}};
    EXPECT_EQ(send(model, command).status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    EXPECT_EQ(model.snapshot(), before);
    command.second->face = face->id;
    ASSERT_EQ(send(model, command).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(model.revision(), before.revision + 1);
    const auto after = model.snapshot().independent->meshes.at(*model.boardFor(1));
    ASSERT_TRUE(Luminophore::Spatial::regionsForOwnership(
        after, Luminophore::Spatial::computeOwnership(model.snapshot().independent->state.boards.at(*model.boardFor(1)), after.fillFocus)));
}

TEST(LuminophoreSpatialIndependent, UnusedFocusClaimPreservesCompetitorThroughProjection) {
    auto model = independent();
    for (uint64_t key = 1; key <= 3; ++key) {
        send(model, SObserveWindowCommand{.key = key, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
        send(model, SMoveWindowToCommand{key, {int64_t(key + 10), 10}, 10, 1});
    }
    send(model, SMoveWindowToCommand{1, {0, 1}, 10, 1});
    send(model, SMoveWindowToCommand{2, {1, 0}, 10, 1});
    send(model, SMoveWindowToCommand{3, {2, 1}, 10, 1});
    send(model, SResizeOutputViewCommand{10, {{0, 0}, 3, 2}, 1});
    send(model, SSpatialFocusCommand{1, 10});
    const auto  before  = model.snapshot();
    const auto  id      = *model.boardFor(1);
    const auto& mesh    = before.independent->meshes.at(id);
    const auto  regions = Luminophore::Spatial::regionsForOwnership(mesh, Luminophore::Spatial::computeOwnership(before.independent->state.boards.at(id), mesh.fillFocus));
    ASSERT_TRUE(regions);
    for (int repeat = 0; repeat < 5; ++repeat)
        for (const auto focus : {2UL, 1UL}) {
            send(model, SSpatialFocusCommand{focus, 10});
            const auto  after   = model.snapshot();
            const auto& current = after.independent->meshes.at(id);
            const auto  actual =
                Luminophore::Spatial::regionsForOwnership(current, Luminophore::Spatial::computeOwnership(after.independent->state.boards.at(id), current.fillFocus));
            ASSERT_TRUE(actual);
            ASSERT_EQ(actual->size(), regions->size());
            for (size_t i = 0; i < actual->size(); ++i) {
                EXPECT_EQ(actual->at(i).owner, regions->at(i).owner);
                EXPECT_EQ(actual->at(i).boxes, regions->at(i).boxes);
            }
            const auto plan = CLuminophoreSpatialProjection::plan(after, 1, {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}});
            ASSERT_TRUE(plan);
            ASSERT_TRUE(CLuminophoreSpatialCommitter::prepare(*plan));
            for (const auto& window : plan->windows)
                EXPECT_EQ(window.fragments.size(), 1);
        }
}

TEST(LuminophoreSpatialIndependent, EditorFloatingCandidateMovesThroughProjectionWithoutMutatingSource) {
    auto                            model = independent();
    const SLuminophoreNormalizedBox box{100, 100, 500, 400};
    ASSERT_EQ(send(model, SAttachFloatingCommand{.key = 42, .host = {0, 0}, .localBox = box, .outputID = 10}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
    const auto                       original  = model.snapshot();
    auto                             candidate = model;
    const SLuminophoreSpatialCommand move{.expectedRevision = model.revision(), .payload = SUpdateFloatingCommand{.key = 42, .host = {1, 1}, .localBox = box, .outputID = 20}};
    const auto                       result = Luminophore::applyEditorCandidate(candidate, move);
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(model.revision(), original.revision);
    EXPECT_EQ(model.outputFor(42), 10U);
    EXPECT_EQ(candidate.outputFor(42), 20U);
    const std::vector<SLuminophorePhysicalOutput> outputs{{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}};
    const auto                                    plan = CLuminophoreSpatialProjection::plan(result.snapshot, 1, outputs);
    ASSERT_TRUE(plan);
    EXPECT_TRUE(CLuminophoreSpatialCommitter::prepare(*plan));
    EXPECT_EQ(Luminophore::applyEditorCandidate(candidate, move).status, eLuminophoreSpatialTransactionStatus::STALE_REVISION);
    EXPECT_EQ(Luminophore::applyEditorCandidate(candidate, {candidate.revision(), SDetachFloatingCommand{42}}).status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
}

TEST(LuminophoreSpatialIndependent, RevealMovesOnlyOwnersViewAndPreservesWindowAnchor) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(m, SMoveOutputViewToCommand{20, {8, 7}, 1});
    const auto anchor = m.coordinateOf(1);
    const auto before = m.snapshot().independent->state;
    const auto board  = *m.boardFor(1);
    const auto result = send(m, SSpatialFocusCommand{.key = 1, .reveal = true});
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_TRUE(result.updatesFocus);
    EXPECT_EQ(result.nextFocusedKey, 1);
    EXPECT_EQ(m.coordinateOf(1), anchor);
    EXPECT_EQ(m.outputFor(1), 20);
    const auto after = m.snapshot().independent->state;
    EXPECT_EQ(after.boards.at(board).view.columns, before.boards.at(board).view.columns);
    EXPECT_EQ(after.boards.at(board).view.rows, before.boards.at(board).view.rows);
    EXPECT_TRUE(after.boards.at(board).view.contains({anchor->x, anchor->y}));
    for (const auto& [id, value] : before.boards) {
        if (id == board)
            continue;
        EXPECT_EQ(after.boards.at(id), value);
    }
    const auto stable = m.snapshot();
    EXPECT_EQ(send(m, SSpatialFocusCommand{.key = 1, .reveal = true}).status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_EQ(m.snapshot(), stable);
    EXPECT_EQ(send(m, SSpatialFocusCommand{.key = 999, .reveal = true}).status, eLuminophoreSpatialTransactionStatus::INVALID_COMMAND);
    EXPECT_EQ(m.snapshot(), stable);
}

TEST(LuminophoreSpatialIndependent, RevealExitsDesktopAndOtherWindowsWide) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(m, SEnterWideCommand{2});
    send(m, SSpatialFocusCommand{.key = 1, .reveal = true});
    EXPECT_EQ(m.snapshot().presentationMode, eLuminophorePresentationMode::NORMAL);
    send(m, SToggleDesktopCommand{});
    send(m, SSpatialFocusCommand{.key = 1, .reveal = true});
    EXPECT_EQ(m.snapshot().presentationMode, eLuminophorePresentationMode::NORMAL);
}

TEST(LuminophoreSpatialIndependent, RevealAlreadyWideSourceLeavesUnderlyingViewAlone) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SMoveOutputViewToCommand{10, {8, 7}, 1});
    send(m, SEnterWideCommand{1});
    const auto board = *m.boardFor(1);
    const auto view  = m.snapshot().independent->state.boards.at(board).view;
    send(m, SSpatialFocusCommand{.key = 1, .reveal = true});
    EXPECT_EQ(m.snapshot().presentationMode, eLuminophorePresentationMode::WIDE);
    EXPECT_EQ(m.snapshot().independent->state.boards.at(board).view, view);
}

TEST(LuminophoreSpatialIndependent, CausalSpawnUsesSourcesCurrentBoardAndAnchor) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SMoveWindowToCommand{1, {7, 3}, 20, 1});
    const auto before = m.revision();
    ASSERT_EQ(send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .causalSource = 1}).status,
              eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(m.outputFor(2), 20);
    EXPECT_EQ(m.coordinateOf(1), (SLuminophoreBoardPoint{7, 3}));
    const auto p = m.coordinateOf(2);
    ASSERT_TRUE(p);
    EXPECT_EQ(*p, (SLuminophoreBoardPoint{8, 3}));
    EXPECT_EQ(m.revision(), before + 1);
}
TEST(LuminophoreSpatialIndependent, MissingCausalSourceKeepsDefaultPlacement) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    auto baseline = m;
    send(baseline, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .causalSource = 999});
    EXPECT_EQ(m.snapshot().independent->state, baseline.snapshot().independent->state);
}
TEST(LuminophoreSpatialIndependent, CausalObserveNeverRelocatesExistingWindow) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    const auto position = m.coordinateOf(2);
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .causalSource = 1});
    EXPECT_EQ(m.coordinateOf(2), position);
    EXPECT_EQ(m.outputFor(2), 20);
}
TEST(LuminophoreSpatialIndependent, CausalWideSpawnPreservesBothViews) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(m, SEnterWideCommand{1});
    const auto before = m.snapshot().independent->state.boards;
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .causalSource = 1});
    EXPECT_EQ(m.outputFor(2), 20);
    for (const auto& [id, b] : before)
        EXPECT_EQ(m.snapshot().independent->state.boards.at(id).view, b.view);
}
TEST(LuminophoreSpatialIndependent, FloatingDoesNotFollowCausalSource) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{4, 2}, .outputID = 10, .causalSource = 1});
    EXPECT_EQ(m.outputFor(2), 10);
    EXPECT_EQ(m.floatingHostOf(2), (SLuminophoreBoardPoint{4, 2}));
}

TEST(LuminophoreSpatialIndependent, CausalSpawnAfterCloseIsIdenticalToDefault) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::ABSENT, .closed = true});
    auto baseline = m;
    send(baseline, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .causalSource = 1});
    EXPECT_EQ(m.snapshot().independent->state, baseline.snapshot().independent->state);
}
TEST(LuminophoreSpatialIndependent, CausalConcurrentSpawnsKeepUniqueOccupancyAndFocus) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(m, SSpatialFocusCommand{1, 20});
    for (uint64_t key = 2; key < 10; ++key) {
        ASSERT_EQ(send(m, SObserveWindowCommand{.key = key, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .causalSource = 1}).status,
                  eLuminophoreSpatialTransactionStatus::APPLIED);
        EXPECT_EQ(m.outputFor(key), 20);
    }
    const auto state = m.snapshot().independent->state;
    EXPECT_TRUE(Luminophore::Spatial::validate(state));
    EXPECT_EQ(state.focus, 1);
    EXPECT_EQ(state.boards.at(*m.boardFor(1)).tiled.size(), 9);
}

TEST(LuminophoreSpatialIndependent, DirectAppRuleUsesCurrentViewOnlyOnce) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    send(m, SMoveOutputViewToCommand{10, {8, 7}, 1});
    ASSERT_EQ(send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .initialPlacement = "left", .directLaunch = true}).status,
              eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(m.outputFor(2), 10);
    EXPECT_EQ(m.coordinateOf(2)->x, 7);
    send(m, SMoveWindowToCommand{2, {20, 30}, 20, 1});
    const auto state = m.snapshot();
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .initialPlacement = "left", .directLaunch = true});
    EXPECT_EQ(m.snapshot(), state);
}

TEST(LuminophoreSpatialIndependent, AppPlacementLeavesFloatingGeometryAndWideViewAlone) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SEnterWideCommand{1});
    const auto views = m.snapshot().independent->state.boards;
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .initialPlacement = "right", .directLaunch = true});
    for (const auto& [id, board] : views)
        EXPECT_EQ(m.snapshot().independent->state.boards.at(id).view, board.view);
    send(m,
         SObserveWindowCommand{.key = 3, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{4, 2}, .outputID = 20, .initialPlacement = "left"});
    EXPECT_EQ(m.outputFor(3), 20);
    EXPECT_EQ(m.floatingHostOf(3), (SLuminophoreBoardPoint{4, 2}));
}

TEST(LuminophoreSpatialIndependent, CausalOriginOverridesEvenConfirmedDirectAppRule) {
    auto m = independent();
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    const auto source = *m.coordinateOf(1);
    send(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .causalSource = 1, .initialPlacement = "left", .directLaunch = true});
    EXPECT_EQ(m.outputFor(2), 20);
    EXPECT_EQ(m.coordinateOf(2), (SLuminophoreBoardPoint{source.x + 1, source.y}));
}
TEST(LuminophoreSpatialIndependent, UnknownOriginNeverUsesAppRule) {
    auto m        = independent();
    auto expected = m;
    send(expected, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    send(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10, .initialPlacement = "left"});
    EXPECT_EQ(m.snapshot(), expected.snapshot());
}
