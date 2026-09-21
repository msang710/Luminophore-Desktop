#include "../../src/luminophore/LuminophoreSpatialHistory.hpp"
#include "../../src/luminophore/LuminophoreSpatialTransaction.hpp"
#include "../../src/luminophore/LuminophoreSpatialProjection.hpp"
#include <gtest/gtest.h>
#include <algorithm>

static void change(CLuminophoreSpatialModel& m, LuminophoreSpatialPayload p) {
    ASSERT_EQ(m.transact({m.revision(), std::move(p)}).status, eLuminophoreSpatialTransactionStatus::APPLIED);
}
static CLuminophoreSpatialModel historyModel() {
    CLuminophoreSpatialModel m;
    change(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {20, "DP-2", 1000, 0, 1000, 800}}, 1});
    change(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    return m;
}
static SLuminophoreHistoryFrame frame(const CLuminophoreSpatialModel& m, std::map<LuminophoreWindowKey, uint64_t> lives = {{1, 100}}) {
    return {m.snapshot(), std::move(lives)};
}
static bool accept(const SLuminophoreSpatialSnapshot&) {
    return true;
}
TEST(LuminophoreSpatialHistory, UndoRedoRestoresViewAndMovesNoncollidingNewWindowOutside) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    const auto          before = frame(m);
    change(m, SMoveOutputViewToCommand{10, {5, 5}, 1});
    h.record(before, frame(m));
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    change(m, SMoveWindowToCommand{2, {1, 1}, 10, 1});
    const auto live        = std::map<LuminophoreWindowKey, uint64_t>{{1, 100}, {2, 200}};
    const auto newPosition = m.coordinateOf(2);
    const auto rev         = m.revision();
    ASSERT_EQ(h.replay(false, m, live, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_GT(m.revision(), rev);
    EXPECT_EQ(m.snapshot().independent->state.boards.at(*m.boardFor(1)).view, before.snapshot.independent->state.boards.at(*m.boardFor(1)).view);
    const auto  restored = m.snapshot();
    const auto& b        = restored.independent->state.boards.at(*m.boardFor(2));
    const auto  point    = m.coordinateOf(2);
    ASSERT_TRUE(point);
    EXPECT_FALSE(b.view.contains({point->x, point->y}));
    ASSERT_EQ(h.replay(true, m, live, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(m.coordinateOf(2), newPosition);
}
TEST(LuminophoreSpatialHistory, FailedCommitDoesNotConsumeOrModifyAnything) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                before = frame(m);
    change(m, SMoveWindowToCommand{1, {7, 7}, 10, 1});
    h.record(before, frame(m));
    const auto snapshot = m.snapshot();
    EXPECT_EQ(h.replay(false, m, {{1, 100}}, [](const auto&) { return false; }), eLuminophoreHistoryResult::COMMIT_FAILED);
    EXPECT_EQ(m.snapshot(), snapshot);
    EXPECT_EQ(h.undoCount(), 1);
    EXPECT_EQ(h.redoCount(), 0);
}
TEST(LuminophoreSpatialHistory, ClosedAndRecycledAddressesAreNeverRestoredAsOldWindows) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                before = frame(m);
    change(m, SMoveWindowToCommand{1, {5, 5}, 10, 1});
    h.record(before, frame(m));
    change(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::ABSENT, .closed = true});
    change(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    ASSERT_EQ(h.replay(false, m, {{1, 999}}, accept), eLuminophoreHistoryResult::APPLIED);
    auto point = m.coordinateOf(1);
    ASSERT_TRUE(point);
    const auto savedView = before.snapshot.independent->state.boards.at(*m.boardFor(1)).view;
    EXPECT_FALSE(savedView.contains({point->x, point->y}));
}
TEST(LuminophoreSpatialHistory, MissingMonitorRejectsAndReconnectionUsesCurrentOutputIdentity) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                before = frame(m);
    change(m, SMoveWindowToCommand{1, {3, 3}, 20, 1});
    h.record(before, frame(m));
    change(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}}, 2});
    const auto current = m.snapshot();
    EXPECT_EQ(h.replay(false, m, {{1, 100}}, accept), eLuminophoreHistoryResult::UNAVAILABLE);
    EXPECT_EQ(m.snapshot(), current);
    EXPECT_EQ(h.undoCount(), 1);
    change(m, SSpatialTopologyCommand{{{10, "DP-1", 0, 0, 1000, 800}, {30, "DP-2", 1000, 0, 1000, 800}}, 3});
    ASSERT_EQ(h.replay(false, m, {{1, 100}}, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_TRUE(m.snapshot().independent->outputs.contains(30));
    EXPECT_FALSE(m.snapshot().independent->outputs.contains(20));
    EXPECT_EQ(m.outputTopologyRevision(), 3);
}
TEST(LuminophoreSpatialHistory, NoopPreservesRedoAndAutomaticChangeClearsIt) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                before = frame(m);
    change(m, SMoveWindowToCommand{1, {5, 5}, 10, 1});
    h.record(before, frame(m));
    ASSERT_EQ(h.replay(false, m, {{1, 100}}, accept), eLuminophoreHistoryResult::APPLIED);
    h.record(frame(m), frame(m));
    EXPECT_EQ(h.redoCount(), 1);
    auto a = frame(m);
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    h.record(a, frame(m, {{1, 100}, {2, 200}}));
    EXPECT_EQ(h.redoCount(), 0);
}
TEST(LuminophoreSpatialHistory, CountAndMemoryLimitsEvictOldest) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h(2);
    CLuminophoreSpatialHistory tiny(100, 1);
    for (int n = 1; n <= 4; ++n) {
        auto a = frame(m);
        change(m, SMoveWindowToCommand{1, {n, 0}, 10, 1});
        h.record(a, frame(m));
        tiny.record(a, frame(m));
    }
    EXPECT_EQ(h.undoCount(), 2);
    EXPECT_EQ(tiny.undoCount(), 0);
    EXPECT_EQ(tiny.bytes(), 0);
}
TEST(LuminophoreSpatialHistory, ReplayCallbackCannotRecursivelyConsumeAnotherEntry) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                a = frame(m);
    change(m, SMoveWindowToCommand{1, {5, 5}, 10, 1});
    h.record(a, frame(m));
    EXPECT_EQ(h.replay(false, m, {{1, 100}},
                       [&](const auto&) {
                           EXPECT_EQ(h.replay(false, m, {{1, 100}}, accept), eLuminophoreHistoryResult::BUSY);
                           return true;
                       }),
              eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(h.undoCount(), 0);
}
TEST(LuminophoreSpatialHistory, FloatingNewWindowIsSuppressedOutsideRestoredView) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                a = frame(m);
    change(m, SMoveOutputViewToCommand{10, {5, 5}, 1});
    h.record(a, frame(m));
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::FLOATING, .localBox = SLuminophoreNormalizedBox{0, 0, 20000, 20000}, .outputID = 10});
    ASSERT_EQ(h.replay(false, m, {{1, 100}, {2, 200}}, accept), eLuminophoreHistoryResult::APPLIED);
    auto p = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}});
    ASSERT_TRUE(p);
    const auto w = std::ranges::find(p->windows, 2, &SLuminophoreProjectedWindow::key);
    ASSERT_NE(w, p->windows.end());
    EXPECT_TRUE(w->fragments.empty());
}
TEST(LuminophoreSpatialHistory, ResizeMeshSurvivesUndoRedo) {
    auto m = historyModel();
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    auto       a     = frame(m, {{1, 100}, {2, 200}});
    const auto board = *m.boardFor(1);
    const auto mesh  = m.snapshot().independent->meshes.at(board);
    auto       face  = std::ranges::find_if(mesh.faces, [](auto& f) { return f.provenance.origin == Luminophore::Spatial::SPoint{0, 0}; });
    ASSERT_NE(face, mesh.faces.end());
    change(m, SSpatialResizeCommand{1, 1, 100, face->id});
    auto                resized = m.snapshot().independent->meshes.at(board).faces;
    CLuminophoreSpatialHistory h;
    h.record(a, frame(m, {{1, 100}, {2, 200}}));
    ASSERT_EQ(h.replay(false, m, {{1, 100}, {2, 200}}, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(m.snapshot().independent->meshes.at(board).faces, mesh.faces);
    ASSERT_EQ(h.replay(true, m, {{1, 100}, {2, 200}}, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(m.snapshot().independent->meshes.at(board).faces, resized);
}
TEST(LuminophoreSpatialHistory, MinimizedMappedWindowCanReturnWithoutAppCreation) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                a = frame(m);
    a.native[1]           = {};
    change(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::ABSENT});
    auto minimized                = frame(m);
    minimized.native[1].minimized = true;
    h.record(a, minimized);
    ASSERT_EQ(h.replayNative(false, m, minimized,
                             [&](const auto& s, const auto& target) {
                                 EXPECT_FALSE(target.native.at(1).minimized);
                                 EXPECT_EQ(s.tiled.size(), 1);
                                 return true;
                             }),
              eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(m.coordinateOf(1), (SLuminophoreBoardPoint{0, 0}));
    ASSERT_EQ(h.replayNative(true, m, a,
                             [&](const auto& s, const auto& target) {
                                 EXPECT_TRUE(target.native.at(1).minimized);
                                 EXPECT_TRUE(s.tiled.empty());
                                 return true;
                             }),
              eLuminophoreHistoryResult::APPLIED);
    EXPECT_FALSE(m.coordinateOf(1));
}
TEST(LuminophoreSpatialHistory, NativeOnlyFullscreenTransitionIsAnEntryAndFailureRetainsIt) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                a          = frame(m);
    a.native[1]                    = {};
    auto b                         = a;
    b.native[1].internalFullscreen = 2;
    h.record(a, b);
    ASSERT_EQ(h.undoCount(), 1);
    EXPECT_EQ(h.replayNative(false, m, b,
                             [&](const auto&, const auto& target) {
                                 EXPECT_EQ(target.native.at(1).internalFullscreen, 0);
                                 return false;
                             }),
              eLuminophoreHistoryResult::COMMIT_FAILED);
    EXPECT_EQ(h.undoCount(), 1);
}
TEST(LuminophoreSpatialHistory, DeadWindowIsSkippedAndAlreadyOutsideNewWindowDoesNotMove) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                a = frame(m);
    change(m, SMoveOutputViewToCommand{10, {5, 5}, 1});
    h.record(a, frame(m));
    change(m, SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::ABSENT, .closed = true});
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    change(m, SMoveWindowToCommand{2, {-100, -100}, 10, 1});
    ASSERT_EQ(h.replay(false, m, {{2, 200}}, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_FALSE(m.coordinateOf(1));
    EXPECT_EQ(m.coordinateOf(2), (SLuminophoreBoardPoint{-100, -100}));
}
TEST(LuminophoreSpatialHistory, ReplayingWidePreservesOffViewNewWindow) {
    auto m = historyModel();
    change(m, SEnterWideCommand{1});
    CLuminophoreSpatialHistory h;
    auto                a = frame(m);
    change(m, SExitWideCommand{});
    h.record(a, frame(m));
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    ASSERT_EQ(h.replay(false, m, {{1, 100}, {2, 200}}, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(m.snapshot().presentationMode, eLuminophorePresentationMode::WIDE);
    EXPECT_EQ(m.snapshot().wideKey, 1);
    const auto point = m.coordinateOf(2);
    ASSERT_TRUE(point);
    EXPECT_FALSE(m.snapshot().independent->state.boards.at(*m.boardFor(2)).view.contains({point->x, point->y}));
}
TEST(LuminophoreSpatialHistory, AuxiliaryGestureDoesNotAbsorbAnInterleavedWindowCreation) {
    auto m          = historyModel();
    auto start      = frame(m, {{1, 100}, {9, 900}});
    start.native[9] = {.floating = true, .auxiliary = true, .x = 10, .y = 20, .width = 400, .height = 300};
    CLuminophoreHistoryGeometryGesture gesture;
    ASSERT_TRUE(gesture.begin(start, 9));
    auto middle          = start;
    middle.native[9].x   = 50;
    auto beforeAutomatic = middle;
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    auto afterAutomatic         = middle;
    afterAutomatic.snapshot     = m.snapshot();
    afterAutomatic.lifetimes[2] = 200;
    gesture.mask(beforeAutomatic);
    gesture.mask(afterAutomatic);
    EXPECT_EQ(beforeAutomatic.native.at(9).x, 10);
    EXPECT_EQ(afterAutomatic.native.at(9).x, 10);
    auto final        = afterAutomatic;
    final.native[9].x = 90;
    auto action       = gesture.finish(final);
    ASSERT_TRUE(action);
    EXPECT_EQ(action->first.snapshot, final.snapshot);
    EXPECT_EQ(action->first.lifetimes.at(2), 200);
    EXPECT_EQ(action->first.native.at(9).x, 10);
    EXPECT_EQ(action->second.native.at(9).x, 90);
    EXPECT_FALSE(gesture.active());
    EXPECT_FALSE(gesture.finish(final));
}
TEST(LuminophoreSpatialHistory, AuxiliaryGestureCannotMoveARecycledWindow) {
    auto m          = historyModel();
    auto start      = frame(m, {{9, 900}});
    start.native[9] = {.auxiliary = true, .x = 10};
    CLuminophoreHistoryGeometryGesture g;
    ASSERT_TRUE(g.begin(start, 9));
    auto current         = start;
    current.lifetimes[9] = 901;
    current.native[9].x  = 100;
    auto action          = g.finish(current);
    ASSERT_TRUE(action);
    EXPECT_EQ(action->first.native, action->second.native);
}
TEST(LuminophoreSpatialHistory, PureFocusChangesDoNotClearRedo) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                a = frame(m);
    change(m, SMoveWindowToCommand{1, {5, 5}, 10, 1});
    h.record(a, frame(m));
    ASSERT_EQ(h.replay(false, m, {{1, 100}}, accept), eLuminophoreHistoryResult::APPLIED);
    auto before                             = frame(m);
    auto after                              = before;
    after.focus                             = 1;
    after.snapshot.independent->state.focus = 1;
    ++after.snapshot.independent->focusRevision;
    h.record(before, after);
    EXPECT_EQ(h.redoCount(), 1);
}
TEST(LuminophoreSpatialHistory, DuplicateIpcRequestReplaysReceiptWithoutAnotherMutation) {
    CLuminophoreHistoryRequests requests;
    int                  count = 0;
    auto                 op    = [&] {
        ++count;
        return eLuminophoreHistoryResult::APPLIED;
    };
    EXPECT_EQ(requests.run(false, 5, 5, "request-1", op), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(requests.run(false, 6, 5, "request-1", op), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(count, 1);
    EXPECT_EQ(requests.run(true, 6, 5, "request-1", op), eLuminophoreHistoryResult::UNAVAILABLE);
    EXPECT_EQ(requests.run(false, 6, 5, "request-2", op), eLuminophoreHistoryResult::STALE);
    EXPECT_EQ(count, 1);
}
TEST(LuminophoreSpatialHistory, BusyReceiptDoesNotBecomeADelayedUndoAfterRelease) {
    CLuminophoreHistoryRequests requests;
    int                  count = 0;
    EXPECT_EQ(requests.run(false, 5, 5, "busy", [] { return eLuminophoreHistoryResult::BUSY; }), eLuminophoreHistoryResult::BUSY);
    EXPECT_EQ(requests.run(false, 5, 5, "busy",
                           [&] {
                               ++count;
                               return eLuminophoreHistoryResult::APPLIED;
                           }),
              eLuminophoreHistoryResult::BUSY);
    EXPECT_EQ(count, 0);
}
TEST(LuminophoreSpatialHistory, PartiallyWrittenNativeBatchIsCompensatedWithoutConsumingUndo) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    auto                a = frame(m);
    change(m, SMoveWindowToCommand{1, {5, 5}, 10, 1});
    h.record(a, frame(m));
    std::vector<int> physical{10, 20};
    const auto       before = m.snapshot();
    auto             result = h.replay(false, m, {{1, 100}}, [&](const auto&) {
        return luminophoreApplyHistoryBatch(
                   [&] {
                       physical[0] = 50;
                       return false;
                   },
                   [&] {
                       physical = {10, 20};
                       return true;
                   }) == eLuminophoreHistoryApplyResult::APPLIED;
    });
    EXPECT_EQ(result, eLuminophoreHistoryResult::COMMIT_FAILED);
    EXPECT_EQ(physical, (std::vector<int>{10, 20}));
    EXPECT_EQ(m.snapshot(), before);
    EXPECT_EQ(h.undoCount(), 1);
}
TEST(LuminophoreSpatialHistory, FailedNativeCompensationIsExplicitlyDegraded) {
    EXPECT_EQ(luminophoreApplyHistoryBatch([] { return false; }, [] { return false; }), eLuminophoreHistoryApplyResult::DEGRADED);
    bool recovered = false;
    EXPECT_EQ(luminophoreApplyHistoryBatch([]() -> bool { throw std::runtime_error("injected native failure"); },
                                    [&] {
                                        recovered = true;
                                        return true;
                                    }),
              eLuminophoreHistoryApplyResult::ROLLED_BACK);
    EXPECT_TRUE(recovered);
}

TEST(LuminophoreSpatialHistory, NewUnmanagedWindowRejectsBeforeAnyNativeWrite) {
    auto                m = historyModel();
    CLuminophoreSpatialHistory h;
    const auto          before = frame(m);
    change(m, SMoveOutputViewToCommand{10, {5, 5}, 1});
    h.record(before, frame(m));
    auto current                = frame(m, {{1, 100}, {2, 200}});
    current.native[2].auxiliary = true;
    const auto unchanged        = m.snapshot();
    bool       called           = false;
    EXPECT_EQ(h.replayNative(false, m, current,
                             [&](const auto&, const auto&) {
                                 called = true;
                                 return true;
                             }),
              eLuminophoreHistoryResult::UNAVAILABLE);
    EXPECT_FALSE(called);
    EXPECT_EQ(m.snapshot(), unchanged);
    EXPECT_EQ(h.undoCount(), 1U);
}

TEST(LuminophoreSpatialHistory, CompressedFloatingHostsHaveDistinctPositionsAndKeepClientSize) {
    auto m = historyModel();
    change(m, SResizeOutputViewCommand{.outputID = 10, .rect = {.origin = {}, .columns = 3, .rows = 3}, .expectedTopologyRevision = 1});
    const std::vector<SLuminophorePhysicalOutput> outputs = {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}};
    const auto                             left    = CLuminophoreSpatialProjection::hostBox(m.snapshot(), {1, 1}, outputs[0]);
    const auto                             right   = CLuminophoreSpatialProjection::hostBox(m.snapshot(), {2, 1}, outputs[0]);
    ASSERT_TRUE(left);
    ASSERT_TRUE(right);
    EXPECT_LT(left->x, right->x);
    EXPECT_LE(left->x + left->width, right->x);
    const auto local = CLuminophoreSpatialProjection::normalize({.x = left->x, .y = left->y, .width = 706, .height = 830}, *left);
    ASSERT_TRUE(local);
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{1, 1}, .localBox = *local, .outputID = 10});
    const auto before = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(before);
    change(m, SMoveWindowToCommand{.key = 2, .point = {2, 1}, .outputID = 10, .expectedTopologyRevision = 1});
    const auto after = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(after);
    const auto a = std::ranges::find(before->windows, 2, &SLuminophoreProjectedWindow::key);
    const auto b = std::ranges::find(after->windows, 2, &SLuminophoreProjectedWindow::key);
    ASSERT_NE(a, before->windows.end());
    ASSERT_NE(b, after->windows.end());
    ASSERT_TRUE(a->clientBox);
    ASSERT_TRUE(b->clientBox);
    EXPECT_LT(a->clientBox->x, b->clientBox->x);
    EXPECT_EQ(b->clientBox->width, 706);
    EXPECT_EQ(b->clientBox->height, 830);
}

TEST(LuminophoreSpatialHistory, FloatingReentryUsesStoredDimensionsAfterHiddenCommit) {
    auto                                   m       = historyModel();
    const std::vector<SLuminophorePhysicalOutput> outputs = {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}};
    const auto                             host    = CLuminophoreSpatialProjection::hostBox(m.snapshot(), {0, 0}, outputs[0]);
    ASSERT_TRUE(host);
    const auto local = CLuminophoreSpatialProjection::normalize({.x = host->x, .y = host->y, .width = 706, .height = 830}, *host);
    ASSERT_TRUE(local);
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{0, 0}, .localBox = *local, .outputID = 10});
    for (int repeat = 0; repeat < 3; ++repeat) {
        change(m, SMoveWindowToCommand{.key = 2, .point = {9, 9}, .outputID = 10, .expectedTopologyRevision = 1});
        const auto hidden = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
        ASSERT_TRUE(hidden);
        const auto w = std::ranges::find(hidden->windows, 2, &SLuminophoreProjectedWindow::key);
        ASSERT_NE(w, hidden->windows.end());
        EXPECT_FALSE(w->visible);
        EXPECT_FALSE(w->clientBox);
        // Moving the view over the parked host must also reveal it without resize.
        change(m, SMoveOutputViewToCommand{10, {9, 9}, 1});
        const auto included = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
        ASSERT_TRUE(included);
        const auto includedWindow = std::ranges::find(included->windows, 2, &SLuminophoreProjectedWindow::key);
        ASSERT_NE(includedWindow, included->windows.end());
        ASSERT_TRUE(includedWindow->visible);
        ASSERT_TRUE(includedWindow->clientBox);
        EXPECT_EQ(includedWindow->clientBox->width, 706);
        EXPECT_EQ(includedWindow->clientBox->height, 830);
        change(m, SMoveOutputViewToCommand{10, {0, 0}, 1});
        const auto snapshot = m.snapshot();
        const auto saved    = std::ranges::find(snapshot.floating, 2, &SLuminophoreFloatingPlacement::key);
        ASSERT_NE(saved, snapshot.floating.end());
        const auto target = CLuminophoreSpatialProjection::hostBox(snapshot, {0, 0}, outputs[0]);
        ASSERT_TRUE(target);
        const auto restored =
            CLuminophoreSpatialProjection::normalize({.x = target->x, .y = target->y, .width = saved->localBox.logicalWidth, .height = saved->localBox.logicalHeight}, *target);
        ASSERT_TRUE(restored);
        change(m, SUpdateFloatingCommand{.key = 2, .host = {0, 0}, .localBox = *restored, .outputID = 10});
        const auto visible = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
        ASSERT_TRUE(visible);
        const auto returned = std::ranges::find(visible->windows, 2, &SLuminophoreProjectedWindow::key);
        ASSERT_NE(returned, visible->windows.end());
        ASSERT_TRUE(returned->visible);
        ASSERT_TRUE(returned->clientBox);
        EXPECT_EQ(returned->clientBox->width, 706);
        EXPECT_EQ(returned->clientBox->height, 830);
    }
}

TEST(LuminophoreSpatialHistory, AllModesReserveDistinctCoordinatesAndSwapWithoutResizing) {
    auto                     m = historyModel();
    const SLuminophoreNormalizedBox box{.width = 5000, .height = 5000, .logicalWidth = 706, .logicalHeight = 830};
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{0, 0}, .localBox = box, .outputID = 10});
    ASSERT_NE(m.coordinateOf(1), m.floatingHostOf(2));
    change(m, SObserveWindowCommand{.key = 3, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = SLuminophoreBoardPoint{0, 0}, .localBox = box, .outputID = 10});
    ASSERT_NE(m.coordinateOf(1), m.floatingHostOf(3));
    ASSERT_NE(m.floatingHostOf(2), m.floatingHostOf(3));
    const auto source = *m.floatingHostOf(2), target = *m.coordinateOf(1);
    const auto result = m.transact({m.revision(), SMoveWindowToCommand{2, target, 10, 1}});
    ASSERT_EQ(result.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(result.moveResult, eLuminophoreSpatialMoveResult::SWAPPED);
    EXPECT_EQ(m.coordinateOf(1), source);
    EXPECT_EQ(m.floatingHostOf(2), target);
    const auto snapshot = m.snapshot();
    const auto floating = std::ranges::find(snapshot.floating, 2, &SLuminophoreFloatingPlacement::key);
    ASSERT_NE(floating, snapshot.floating.end());
    EXPECT_EQ(floating->localBox.logicalWidth, 706);
    EXPECT_EQ(floating->localBox.logicalHeight, 830);
    EXPECT_TRUE(m.validate());
    const auto other = *m.floatingHostOf(3);
    change(m, SMoveWindowToCommand{2, other, 10, 1});
    EXPECT_EQ(m.floatingHostOf(3), target);
    EXPECT_EQ(m.floatingHostOf(2), other);
}

TEST(LuminophoreSpatialHistory, FloatingOccupancyDoesNotCreateTiledFillRegions) {
    auto m = historyModel();
    change(m, SResizeOutputViewCommand{.outputID = 10, .rect = {.origin = {-1, -1}, .columns = 3, .rows = 3}, .expectedTopologyRevision = 1});
    const std::vector<SLuminophorePhysicalOutput> outputs = {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}};
    const auto                             before  = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(before);
    change(m,
           SObserveWindowCommand{.key       = 2,
                                 .mode      = eLuminophoreWindowPlacementMode::FLOATING,
                                 .preferred = SLuminophoreBoardPoint{0, 0},
                                 .localBox  = SLuminophoreNormalizedBox{.logicalWidth = 706, .logicalHeight = 830},
                                 .outputID  = 10});
    const auto after = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(after);
    EXPECT_EQ(before->windows.front().fragments, after->windows.front().fragments);
    EXPECT_EQ(m.snapshot().independent->state.boards.at(*m.boardFor(1)).tiled.size(), 1U);
    auto state             = m.snapshot();
    state.floating[0].host = *m.coordinateOf(1); // legacy overlapping history frame
    ASSERT_TRUE(m.restoreSpatial(state, {1, 2}));
    EXPECT_NE(m.coordinateOf(1), m.floatingHostOf(2));
    EXPECT_TRUE(m.validate());
}

TEST(LuminophoreSpatialHistory, TiledSwapPreservesBystanderFloatingPhysicalBoxAndPreviewParity) {
    auto m = historyModel();
    change(m, SResizeOutputViewCommand{.outputID = 10, .rect = {.columns = 3, .rows = 3}, .expectedTopologyRevision = 1});
    const std::vector<SLuminophorePhysicalOutput> outputs = {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}};
    change(m,
           SObserveWindowCommand{.key       = 2,
                                 .mode      = eLuminophoreWindowPlacementMode::FLOATING,
                                 .preferred = SLuminophoreBoardPoint{1, 1},
                                 .localBox  = SLuminophoreNormalizedBox{.logicalWidth = 706, .logicalHeight = 830},
                                 .outputID  = 10});
    const auto before = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(before);
    const auto beforeWindow = std::ranges::find(before->windows, 2, &SLuminophoreProjectedWindow::key);
    ASSERT_NE(beforeWindow, before->windows.end());
    ASSERT_TRUE(beforeWindow->clientBox);
    const auto                original = m.snapshot();
    const SLuminophoreSpatialCommand command{m.revision(), SMoveWindowToCommand{1, {1, 1}, 10, 1}};
    const auto                preview = m.preview(command);
    EXPECT_EQ(m.snapshot(), original);
    const auto commit = m.transact(command);
    ASSERT_EQ(commit.status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(preview.snapshot, commit.snapshot);
    EXPECT_EQ(m.floatingHostOf(2), (SLuminophoreBoardPoint{0, 0}));
    const auto after = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(after);
    const auto afterWindow = std::ranges::find(after->windows, 2, &SLuminophoreProjectedWindow::key);
    ASSERT_NE(afterWindow, after->windows.end());
    EXPECT_EQ(beforeWindow->clientBox, afterWindow->clientBox);
}

TEST(LuminophoreSpatialHistory, OccupiedFloatingModeRoundTripKeepsItsCoordinate) {
    auto m = historyModel();
    change(m,
           SObserveWindowCommand{.key       = 2,
                                 .mode      = eLuminophoreWindowPlacementMode::FLOATING,
                                 .preferred = SLuminophoreBoardPoint{0, 0},
                                 .localBox  = SLuminophoreNormalizedBox{.logicalWidth = 706, .logicalHeight = 830},
                                 .outputID  = 10});
    const auto host = *m.floatingHostOf(2);
    change(m, SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10});
    EXPECT_EQ(m.coordinateOf(2), host);
    EXPECT_FALSE(m.floatingHostOf(2));
    change(m,
           SObserveWindowCommand{
               .key = 2, .mode = eLuminophoreWindowPlacementMode::FLOATING, .preferred = host, .localBox = SLuminophoreNormalizedBox{.logicalWidth = 706, .logicalHeight = 830}, .outputID = 10});
    EXPECT_EQ(m.floatingHostOf(2), host);
    EXPECT_FALSE(m.coordinateOf(2));
    EXPECT_TRUE(m.validate());
}

TEST(LuminophoreSpatialHistory, CrossBoardMixedSwapRetainsFloatingSizeAndDestinationVisibility) {
    auto m = historyModel();
    change(m,
           SObserveWindowCommand{.key       = 2,
                                 .mode      = eLuminophoreWindowPlacementMode::FLOATING,
                                 .preferred = SLuminophoreBoardPoint{1, 0},
                                 .localBox  = SLuminophoreNormalizedBox{.logicalWidth = 706, .logicalHeight = 830},
                                 .outputID  = 10});
    change(m, SObserveWindowCommand{.key = 3, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 20});
    const auto source = *m.coordinateOf(3);
    change(m, SMoveWindowToCommand{3, *m.floatingHostOf(2), 10, 1});
    EXPECT_EQ(m.boardFor(2), m.snapshot().independent->bindings.at(20));
    EXPECT_EQ(m.floatingHostOf(2), source);
    const std::vector<SLuminophorePhysicalOutput> outputs = {{10, "DP-1", {0, 0, 1000, 800}}, {20, "DP-2", {1000, 0, 1000, 800}}};
    const auto                             plan    = CLuminophoreSpatialProjection::plan(m.snapshot(), 1, outputs);
    ASSERT_TRUE(plan);
    const auto w = std::ranges::find(plan->windows, 2, &SLuminophoreProjectedWindow::key);
    ASSERT_NE(w, plan->windows.end());
    ASSERT_TRUE(w->visible);
    ASSERT_TRUE(w->clientBox);
    EXPECT_EQ(w->clientBox->width, 706);
    EXPECT_EQ(w->clientBox->height, 830);
    EXPECT_EQ(w->primaryOutputID, 20U);
}

TEST(LuminophoreSpatialHistory, BundleCoalescesUntilUnrelatedActionThenLateArrivalsStaySeparate) {
    auto m = historyModel();
    CLuminophoreSpatialHistory h;
    auto arrive = [&](LuminophoreWindowKey key) {
        auto before = frame(m);
        change(m, SObserveWindowCommand{.key=key, .mode=eLuminophoreWindowPlacementMode::TILED, .outputID=10});
        h.record(before, frame(m), "bundle");
    };
    arrive(2);
    arrive(3);
    EXPECT_EQ(h.undoCount(), 1);
    auto before = frame(m);
    change(m, SMoveOutputViewToCommand{10, {8, 8}, 1});
    h.record(before, frame(m));
    EXPECT_EQ(h.undoCount(), 2);
    arrive(4);
    arrive(5);
    EXPECT_EQ(h.undoCount(), 4);
}
TEST(LuminophoreSpatialHistory, UndoClosesBundleAndNoopDoesNotSplitIt) {
    auto m = historyModel();
    CLuminophoreSpatialHistory h;
    auto before = frame(m);
    change(m, SMoveWindowToCommand{1, {4, 4}, 10, 1});
    h.record(before, frame(m), "bundle");
    h.record(frame(m), frame(m));
    before = frame(m);
    change(m, SMoveWindowToCommand{1, {5, 4}, 10, 1});
    h.record(before, frame(m), "bundle");
    EXPECT_EQ(h.undoCount(), 1);
    ASSERT_EQ(h.replay(false, m, {{1,100}}, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(m.coordinateOf(1), historyModel().coordinateOf(1));
    before = frame(m);
    change(m, SMoveWindowToCommand{1, {6, 4}, 10, 1});
    h.record(before, frame(m), "bundle");
    before = frame(m);
    change(m, SMoveWindowToCommand{1, {7, 4}, 10, 1});
    h.record(before, frame(m), "bundle");
    EXPECT_EQ(h.undoCount(), 2);
    EXPECT_EQ(h.redoCount(), 0);
}

TEST(LuminophoreSpatialHistory, BundleUndoParksAllNewWindowsAndRedoRestoresThem) {
    auto m = historyModel();
    CLuminophoreSpatialHistory h;
    const auto initial = frame(m);
    auto before = initial;
    change(m, SObserveWindowCommand{.key=2, .mode=eLuminophoreWindowPlacementMode::TILED, .outputID=10});
    h.record(before, frame(m, {{1,100},{2,200}}), "bundle");
    before = frame(m, {{1,100},{2,200}});
    change(m, SObserveWindowCommand{.key=3, .mode=eLuminophoreWindowPlacementMode::TILED, .outputID=10});
    const std::map<LuminophoreWindowKey, uint64_t> lives{{1,100},{2,200},{3,300}};
    h.record(before, frame(m, lives), "bundle");
    const auto p2=m.coordinateOf(2), p3=m.coordinateOf(3);
    ASSERT_EQ(h.undoCount(), 1);
    ASSERT_EQ(h.replay(false, m, lives, accept), eLuminophoreHistoryResult::APPLIED);
    for (auto key : {2,3}) {
        const auto point=m.coordinateOf(key);
        ASSERT_TRUE(point);
        const auto snapshot=m.snapshot();
        EXPECT_FALSE(snapshot.independent->state.boards.at(*m.boardFor(key)).view.contains({point->x,point->y}));
    }
    ASSERT_EQ(h.replay(true, m, lives, accept), eLuminophoreHistoryResult::APPLIED);
    EXPECT_EQ(m.coordinateOf(2),p2);
    EXPECT_EQ(m.coordinateOf(3),p3);
}
