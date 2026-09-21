#include "../../src/luminophore/LuminophoreSpatialMotion.hpp"
#include <gtest/gtest.h>
#include <limits>

TEST(LuminophoreSpatialMotion, AllDirectionsTranslateClientAndEveryRegionTogether) {
    const SLuminophoreSpatialCommitEntry entry{.key             = 1,
                                        .visible         = true,
                                        .primaryOutputID = 10,
                                        .clientBox       = {0, 0, 800, 600},
                                        .fragments       = {{.outputID = 10, .box = {0, 0, 400, 600}}, {.outputID = 10, .box = {400, 0, 400, 300}}}};
    const SLuminophoreViewRect           view{{0, 0}, 2, 2};
    const SLuminophorePhysicalBox        output{0, 0, 800, 600};
    for (const auto [point, shift] : std::vector<std::pair<SLuminophoreBoardPoint, SLuminophoreBoardPoint>>{{{-1, 0}, {-800, 0}}, {{2, 0}, {800, 0}}, {{0, -1}, {0, -600}}, {{0, 2}, {0, 600}}}) {
        const auto moved = CLuminophoreSpatialMotion::outside(entry, point, view, output);
        ASSERT_TRUE(moved);
        EXPECT_EQ(moved->clientBox.x, shift.x);
        EXPECT_EQ(moved->clientBox.y, shift.y);
        ASSERT_EQ(moved->fragments.size(), 2U);
        for (size_t i = 0; i < 2; ++i) {
            EXPECT_EQ(moved->fragments[i].box.x, entry.fragments[i].box.x + shift.x);
            EXPECT_EQ(moved->fragments[i].box.y, entry.fragments[i].box.y + shift.y);
        }
    }
    EXPECT_FALSE(CLuminophoreSpatialMotion::outside(entry, {1, 1}, view, output));
    auto overflow        = entry;
    overflow.clientBox.x = std::numeric_limits<int>::max();
    EXPECT_FALSE(CLuminophoreSpatialMotion::outside(overflow, {2, 0}, view, output));
}

TEST(LuminophoreSpatialMotion, SignedEndpointContainmentDoesNotOverflow) {
    const SLuminophoreViewRect view{{INT64_MAX - 1, INT64_MAX - 1}, 2, 2};
    EXPECT_TRUE(view.contains({INT64_MAX, INT64_MAX}));
    EXPECT_FALSE(view.contains({INT64_MIN, INT64_MIN}));
}
