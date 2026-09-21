#include "../../src/luminophore/LuminophoreMonitorLayout.hpp"
#include <gtest/gtest.h>
#include <limits>
using namespace Luminophore::MonitorLayout;
TEST(MonitorLayout, StrictPositionProtocol) {
    EXPECT_TRUE(parse("s-abc -1920 0 c-def 0 0"));
    EXPECT_FALSE(parse("x 0 0 x 1 1"));
    EXPECT_FALSE(parse("x 0"));
    EXPECT_FALSE(parse("x 2147483648 0"));
    EXPECT_FALSE(parse("x nan 0"));
    EXPECT_FALSE(parse("x 1.5 0"));
    EXPECT_FALSE(parse("x -100001 0"));
}
TEST(MonitorLayout, PositionOnlyLayoutHandlesReversedAndVerticalDisplays) {
    std::vector<SOutput> monitors{{"a", 0, 0, 1920, 1080}, {"b", 1920, 0, 1920, 1080}};
    EXPECT_TRUE(validate(monitors, {{"b", {0, 0}}, {"a", {1920, 0}}}));
    EXPECT_TRUE(validate(monitors, {{"b", {0, -1080}}, {"a", {0, 0}}}));
    EXPECT_FALSE(validate(monitors, {{"b", {1900, 0}}, {"a", {0, 0}}}));
    EXPECT_FALSE(validate(monitors, {{"a", {0, 0}}}));
    EXPECT_FALSE(validate(monitors, {{"b", {0, 0}}, {"a", {std::numeric_limits<double>::quiet_NaN(), 0.0}}}));
}

TEST(MonitorLayout, DeadlineCoversBothPreviewAndConfirmationAndTopologyLoss) {
    using namespace std::chrono;
    const auto    start = steady_clock::time_point{};
    SPreviewLease lease{.id = "request", .topology = "original", .phase = "preview", .deadline = start + seconds(15)};
    EXPECT_FALSE(lease.expired(start + seconds(14), "original"));
    EXPECT_TRUE(lease.expired(start + seconds(15), "original"));
    EXPECT_TRUE(lease.expired(start + seconds(1), "unplugged"));
    lease.phase = "confirming";
    EXPECT_TRUE(lease.expired(start + seconds(16), "original"));
    lease.phase = "committed";
    EXPECT_FALSE(lease.expired(start + seconds(16), "original"));
    lease.phase = "rolled_back";
    EXPECT_FALSE(lease.pending());
}
