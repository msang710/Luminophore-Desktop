#include "../../src/luminophore/LuminophoreSpatialPreview.hpp"
#include "../../src/luminophore/LuminophoreSpatialWindowRegistry.hpp"
#include "../../src/luminophore/LuminophoreSpatialNativeAdapter.hpp"
#include <gtest/gtest.h>

TEST(LuminophoreSpatialPreview, ThousandRepeatedCandidatesAreComputedOnce) {
    CLuminophoreSpatialPreviewCache cache;
    SLuminophoreSpatialPreviewKey   key{.generation = 1, .revision = 7, .topologyRevision = 4, .window = 1, .outputID = 10, .point = SLuminophoreBoardPoint{2, 1}};
    int                      calls = 0;
    for (int i = 0; i < 1000; ++i) {
        if (cache.matches(key))
            continue;
        ++calls;
        cache.remember(key);
    }
    EXPECT_EQ(calls, 1);
    cache.clear();
    EXPECT_FALSE(cache.matches(key));
}

TEST(LuminophoreSpatialPreview, EveryContextChangeInvalidatesEvenAnIdenticalCell) {
    const SLuminophoreSpatialPreviewKey original{.generation            = 1,
                                          .revision              = 7,
                                          .topologyRevision      = 4,
                                          .observationGeneration = 5,
                                          .layoutGeneration      = 6,
                                          .window                = 1,
                                          .outputID              = 10,
                                          .point                 = SLuminophoreBoardPoint{2, 1},
                                          .frame                 = SLuminophoreEditorFrame{.generation = "frame", .revision = 8, .outputID = 10, .width = 400, .height = 200}};
    CLuminophoreSpatialPreviewCache     cache;
    cache.remember(original);
    for (int field = 0; field < 13; ++field) {
        auto changed = original;
        switch (field) {
            case 0: ++changed.generation; break;
            case 1: ++changed.revision; break;
            case 2: ++changed.topologyRevision; break;
            case 3: ++changed.observationGeneration; break;
            case 4: ++changed.layoutGeneration; break;
            case 5: ++changed.window; break;
            case 6: ++changed.outputID; break;
            case 7: ++changed.point->x; break;
            case 8: changed.point.reset(); break;
            case 9: changed.frame->generation = "new"; break;
            case 10: ++changed.frame->revision; break;
            case 11: ++changed.frame->x; break;
            case 12: changed.frame.reset(); break;
        }
        EXPECT_FALSE(cache.matches(changed)) << field;
    }
}

TEST(LuminophoreSpatialPreview, LeavingBoardAndReturningCannotRetainTheOutsideCandidate) {
    CLuminophoreSpatialPreviewCache cache;
    SLuminophoreSpatialPreviewKey   key{.window = 1, .point = SLuminophoreBoardPoint{2, 1}};
    cache.remember(key);
    auto outside = key;
    outside.point.reset();
    EXPECT_FALSE(cache.matches(outside));
    cache.remember(outside);
    EXPECT_TRUE(cache.matches(outside));
    EXPECT_FALSE(cache.matches(key));
}

TEST(LuminophoreSpatialWindowRegistry, MotionRecoveryBoxSurvivesEndUntilExplicitlyConsumed) {
    Luminophore::CLuminophoreSpatialWindowRegistry registry;
    const SLuminophorePhysicalBox           first{.x = -10, .y = 20, .width = 160, .height = 60};
    auto                             final = first;
    final.x                                = 100;
    const auto initialGeneration           = registry.generation();
    EXPECT_FALSE(registry.updateMotion(1, first));
    registry.beginMotion(1, first);
    ASSERT_TRUE(registry.activeMotion(1));
    EXPECT_TRUE(registry.updateMotion(1, final));
    registry.endMotion(1);
    EXPECT_FALSE(registry.activeMotion(1));
    EXPECT_FALSE(registry.updateMotion(1, first));
    const auto saved = registry.takeMotion(1);
    ASSERT_TRUE(saved);
    EXPECT_EQ(*saved, final);
    EXPECT_FALSE(registry.hasMotion(1));
    registry.restoreMotion(1, *saved);
    EXPECT_TRUE(registry.hasMotion(1));
    EXPECT_FALSE(registry.activeMotion(1));
    EXPECT_GT(registry.generation(), initialGeneration);
    registry.clearMotion(1);
    EXPECT_FALSE(registry.takeMotion(1));
}

TEST(LuminophoreSpatialWindowRegistry, FloatingFragmentsUseIntersectionsAndPhysicalCenter) {
    Luminophore::CLuminophoreSpatialWindowRegistry registry;
    registry.beginMotion(1, {.x = 80, .y = 10, .width = 100, .height = 50});
    const std::vector<SLuminophorePhysicalOutput> outputs{{.id = 10, .box = {.width = 100, .height = 100}}, {.id = 20, .box = {.x = 100, .width = 100, .height = 100}}};
    const auto                             entry = registry.presentation(1, {2, 1}, outputs);
    ASSERT_TRUE(entry);
    EXPECT_TRUE(entry->visible);
    EXPECT_EQ(entry->primaryOutputID, 20U);
    ASSERT_EQ(entry->fragments.size(), 2U);
    EXPECT_EQ(entry->fragments[0].box.width, 20);
    EXPECT_EQ(entry->fragments[1].box.width, 80);
    EXPECT_EQ(entry->fragments[0].point, (SLuminophoreBoardPoint{2, 1}));
    registry.updateMotion(1, {.x = -300, .y = 10, .width = 100, .height = 50});
    const auto outside = registry.presentation(1, {}, outputs);
    ASSERT_TRUE(outside);
    EXPECT_FALSE(outside->visible);
    EXPECT_EQ(outside->clientBox, SLuminophorePhysicalBox{});
}

TEST(LuminophoreSpatialNativeAdapter, MissingTargetFailsBeforeAnyNativeApplication) {
    Luminophore::CLuminophoreSpatialWindowRegistry registry;
    SLuminophoreSpatialCommit               commit{.modelRevision = 4, .topologyRevision = 2, .entries = {{.key = 1}}};
    EXPECT_FALSE(Luminophore::SpatialNative::CResolvedBatch::prepare(commit, registry));
    EXPECT_FALSE(registry.targetFor(1));
    EXPECT_EQ(commit.entries.size(), 1U);
}

TEST(LuminophoreSpatialPreview, EventLoopBurstKeepsLatestCoordinatesAndOneReservation) {
    CLuminophoreSpatialPreviewQueue queue;
    const auto               token = queue.push(0, 0);
    ASSERT_TRUE(token);
    for (int i = 1; i < 1000; ++i)
        EXPECT_FALSE(queue.push(i, -i));
    const auto latest = queue.take(*token);
    ASSERT_TRUE(latest);
    EXPECT_EQ(*latest, (std::pair<double, double>{999, -999}));
    EXPECT_FALSE(queue.take(*token));
    EXPECT_TRUE(queue.push(1000, 0));
}

TEST(LuminophoreSpatialPreview, ReleaseCancelAndNewGrabInvalidateAlreadyCopiedCallbacks) {
    CLuminophoreSpatialPreviewQueue queue;
    const auto               old = queue.push(10, 20);
    ASSERT_TRUE(old);
    queue.cancel();
    EXPECT_FALSE(queue.take(*old));
    const auto current = queue.push(40, 50);
    ASSERT_TRUE(current);
    EXPECT_FALSE(queue.take(*old));
    EXPECT_TRUE(queue.take(*current));
}

#include "../../src/luminophore/LuminophoreSpatialEvents.hpp"
TEST(LuminophoreSpatialEvents, FocusResultRetainsExactWireContractAndBeforeAfterFacts) {
    const SLuminophoreSpatialSnapshot before{.extent                 = {.columns = 15, .rows = 5},
                                      .tiled                  = {{.key = 1, .point = {0, 0}}, {.key = 2, .point = {5, 1}}},
                                      .outputViews            = {{.outputID = 10, .rect = {.origin = {0, 0}, .columns = 3, .rows = 2}}},
                                      .outputTopologyRevision = 4,
                                      .revision               = 7};
    auto                       after        = before;
    after.revision                          = 8;
    after.outputViews.front().rect.origin.x = 3;
    const SLuminophoreSpatialTransactionResult result{.status = eLuminophoreSpatialTransactionStatus::APPLIED, .updatesFocus = true, .nextFocusedKey = 2, .snapshot = after};
    const auto json = Luminophore::SpatialEvents::serializeAction(Luminophore::eSpatialAction::FOCUS_DIRECTION, eLuminophoreSpatialDirection::RIGHT, before, result, 1, 2, 10, "DP-1", true, "");
    EXPECT_EQ(
        json,
        R"({"schema":1,"action":"focus","applied":true,"direction":"right","visible":true,"reason":"","revision":8,"topologyRevision":4,"output":10,"connector":"DP-1","window":"0x2","fromPoint":[0,0],"toPoint":[5,1],"fromRect":[0,0,3,2],"toRect":[3,0,3,2],"displayOrigin":[7,2]})");
}

TEST(LuminophoreSpatialEvents, RejectedTargetDoesNotInventWindowOrOutputFacts) {
    const SLuminophoreSpatialSnapshot          snapshot{.extent = {.columns = 9, .rows = 5}, .revision = 7};
    const SLuminophoreSpatialTransactionResult result{.status = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND, .snapshot = snapshot};
    const auto json = Luminophore::SpatialEvents::serializeAction(Luminophore::eSpatialAction::MOVE_VIEW, eLuminophoreSpatialDirection::LEFT, snapshot, result, std::nullopt, std::nullopt, 0, "", false,
                                                           "invalid-target");
    EXPECT_EQ(
        json,
        R"({"schema":1,"action":"view-move","applied":false,"direction":"left","visible":false,"reason":"invalid-target","revision":7,"topologyRevision":0,"output":0,"connector":"","window":"0x0","fromPoint":null,"toPoint":null,"fromRect":null,"toRect":null,"displayOrigin":[4,2]})");
}

#include "../../src/layout/target/Target.hpp"
class CRegistryTarget : public Layout::ITarget {
  public:
    Layout::eTargetType type() override {
        return Layout::TARGET_TYPE_WINDOW;
    }
    PHLWINDOW window() const override {
        return nullptr;
    }
    bool floating() override {
        return false;
    }
    void setFloating(bool) override {
        ;
    }
    std::expected<Layout::SGeometryRequested, Layout::eGeometryFailure> desiredGeometry() override {
        return std::unexpected(Layout::GEOMETRY_NO_DESIRED);
    }
    std::optional<Vector2D> minSize() override {
        return std::nullopt;
    }
    std::optional<Vector2D> maxSize() override {
        return std::nullopt;
    }
    void damageEntire() override {
        ;
    }
    void warpPositionSize() override {
        ;
    }
    void onUpdateSpace() override {
        ;
    }
};
TEST(LuminophoreSpatialWindowRegistry, ObservationDoesNotExtendTargetLifetimeOrResolveAnAbsentWindow) {
    Luminophore::CLuminophoreSpatialWindowRegistry registry;
    auto                             target = makeShared<CRegistryTarget>();
    const WP<CRegistryTarget>        weak   = target;
    registry.remember(1, target);
    ASSERT_TRUE(registry.targetFor(1));
    EXPECT_EQ(Luminophore::CLuminophoreSpatialWindowRegistry::participationFor(target), Luminophore::eSpatialParticipation::ABSENT);
    const SLuminophoreSpatialCommit commit{.entries = {{.key = 1}}};
    EXPECT_FALSE(Luminophore::SpatialNative::CResolvedBatch::prepare(commit, registry));
    target.reset();
    EXPECT_FALSE(weak.lock());
    EXPECT_FALSE(registry.targetFor(1));
    registry.forget(1);
    EXPECT_FALSE(registry.targetFor(1));
}
