#include "../../src/luminophore/LuminophoreSpatialCommandQueue.hpp"

#include <gtest/gtest.h>

TEST(LuminophoreSpatialCommandQueue, EventQueuedDuringDrainRunsInNextTransaction) {
    auto                     model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 1});
    CLuminophoreSpatialCommandQueue queue;
    queue.enqueue(SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED});

    bool       queuedDuringDrain = false;
    const auto results           = queue.drain(model, [&](const auto&) {
        if (queuedDuringDrain)
            return;
        queuedDuringDrain = true;
        EXPECT_TRUE(queue.draining());
        queue.enqueue(SObserveWindowCommand{.key = 2, .mode = eLuminophoreWindowPlacementMode::TILED});
    });

    ASSERT_EQ(results.size(), 2);
    EXPECT_EQ(results[0].snapshot.revision, 1);
    EXPECT_EQ(results[1].snapshot.revision, 2);
    EXPECT_EQ(model.tiledWindows(), (std::vector<LuminophoreWindowKey>{1, 2}));
    EXPECT_EQ(queue.pending(), 0);
}

TEST(LuminophoreSpatialCommandQueue, DuplicateLifecycleEventDoesNotPublishARevision) {
    auto                     model = CLuminophoreSpatialModel::finiteFixture({.columns = 3, .rows = 1});
    CLuminophoreSpatialCommandQueue queue;
    queue.enqueue(SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED});
    queue.enqueue(SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED});

    const auto results = queue.drain(model);

    ASSERT_EQ(results.size(), 2);
    EXPECT_EQ(results[0].status, eLuminophoreSpatialTransactionStatus::APPLIED);
    EXPECT_EQ(results[1].status, eLuminophoreSpatialTransactionStatus::NO_CHANGE);
    EXPECT_EQ(model.revision(), 1);
}

TEST(LuminophoreSpatialCommandQueue, TopologyAndLifecycleEventsKeepFifoOrder) {
    auto                     model = CLuminophoreSpatialModel::finiteFixture({.columns = 2, .rows = 1});
    CLuminophoreSpatialCommandQueue queue;
    queue.enqueue(SReconfigureExtentCommand{.extent = {.columns = 4, .rows = 2}});
    queue.enqueue(SObserveWindowCommand{.key = 1, .mode = eLuminophoreWindowPlacementMode::TILED, .preferred = SLuminophoreBoardPoint{.x = 3, .y = 1}});

    const auto results = queue.drain(model);

    ASSERT_EQ(results.size(), 2);
    EXPECT_EQ(results[0].snapshot.extent, (SLuminophoreBoardExtent{.columns = 4, .rows = 2}));
    EXPECT_EQ(results[1].snapshot.revision, 2);
    EXPECT_EQ(model.coordinateOf(1), (SLuminophoreBoardPoint{.x = 3, .y = 1}));
}
