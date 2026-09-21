#include <gtest/gtest.h>
#include "src/luminophore/LuminophoreDragIntentController.hpp"

using namespace std::chrono_literals;

TEST(LuminophoreDragShake, OriginIsAValidFirstSampleAndReleaseStateIsLatched) {
    Luminophore::CLuminophoreDragIntentController tracker;
    const auto                      start = std::chrono::steady_clock::time_point{};
    tracker.begin(start);
    for (int i = 0; i <= 4; ++i)
        tracker.update({i % 2 ? 100.0 : 0.0, 0.0}, true, start + i * 50ms);
    EXPECT_TRUE(tracker.snapshot().shake);
    tracker.update({0, 0}, false, start + 2s);
    EXPECT_TRUE(tracker.snapshot().shake);
    tracker.cancel();
    EXPECT_FALSE(tracker.snapshot().shake);
    EXPECT_EQ(tracker.snapshot().generation, 0U);
}

TEST(LuminophoreDragShake, InactiveIneligibleAndVerticalMotionCannotIsolate) {
    Luminophore::CLuminophoreDragIntentController tracker;
    const auto                      start = std::chrono::steady_clock::time_point{};
    for (int i = 0; i <= 4; ++i)
        tracker.update({i % 2 ? 100.0 : 0.0, 0.0}, true, start + i * 50ms);
    EXPECT_FALSE(tracker.snapshot().shake);
    tracker.begin(start);
    for (int i = 0; i <= 4; ++i)
        tracker.update({i % 2 ? 100.0 : 0.0, 0.0}, false, start + i * 50ms);
    EXPECT_FALSE(tracker.snapshot().shake);
    for (int i = 0; i <= 4; ++i)
        tracker.update({0.0, i % 2 ? 100.0 : 0.0}, true, start + i * 50ms);
    EXPECT_FALSE(tracker.snapshot().shake);
}

TEST(LuminophoreDragShake, TimeWindowAndNewGrabResetAccumulatedTravel) {
    Luminophore::CLuminophoreDragIntentController tracker;
    const auto                      start = std::chrono::steady_clock::time_point{};
    tracker.begin(start);
    const auto generation = tracker.snapshot().generation;
    for (int i = 0; i <= 4; ++i)
        tracker.update({i % 2 ? 100.0 : 0.0, 0.0}, true, start + i * 800ms);
    EXPECT_FALSE(tracker.snapshot().shake);
    tracker.begin(start + 4s);
    EXPECT_GT(tracker.snapshot().generation, generation);
    for (int i = 0; i <= 4; ++i)
        tracker.update({i % 2 ? 100.0 : 0.0, 0.0}, true, start + 4s + i * 50ms);
    EXPECT_TRUE(tracker.snapshot().shake);
    tracker.begin(start + 5s);
    EXPECT_FALSE(tracker.snapshot().shake);
    tracker.update({100, 0}, true, start + 5s);
    EXPECT_FALSE(tracker.snapshot().shake);
}
