#include "../../src/desktop/view/Window.hpp"
#include "../../src/desktop/view/WindowInputPolicy.hpp"

#include <gtest/gtest.h>

using namespace Desktop::View;

TEST(WindowInputPolicy, OutsideViewAlonePreservesFocusAcrossRepeatedFullscreenClears) {
    uint32_t reasons = INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW;
    for (int i = 0; i < 3; ++i) {
        reasons &= ~uint32_t(INPUT_BLOCK_BELOW_FULLSCREEN);
        EXPECT_FALSE(inputBlockRevokesFocus(reasons, false));
        EXPECT_NE(reasons & INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW, 0U);
    }
}

TEST(WindowInputPolicy, ActualFocusBlocksRemainEffectiveWithOrWithoutSpatialBlock) {
    for (const auto reason : {INPUT_BLOCK_MONOCLE_INACTIVE, INPUT_BLOCK_BELOW_FULLSCREEN}) {
        EXPECT_TRUE(inputBlockRevokesFocus(reason, false));
        EXPECT_TRUE(inputBlockRevokesFocus(reason | INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW, false));
    }
}

TEST(WindowInputPolicy, RemovingOneReasonDoesNotIgnoreOtherFocusBlocks) {
    uint32_t reasons = INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW | INPUT_BLOCK_BELOW_FULLSCREEN | INPUT_BLOCK_MONOCLE_INACTIVE;
    reasons &= ~uint32_t(INPUT_BLOCK_BELOW_FULLSCREEN);
    EXPECT_TRUE(inputBlockRevokesFocus(reasons, false));
    reasons &= ~uint32_t(INPUT_BLOCK_MONOCLE_INACTIVE);
    EXPECT_FALSE(inputBlockRevokesFocus(reasons, false));
    reasons |= INPUT_BLOCK_BELOW_FULLSCREEN;
    EXPECT_TRUE(inputBlockRevokesFocus(reasons, false));
}

TEST(WindowInputPolicy, DesktopExposureRemainsAFocusBlock) {
    EXPECT_FALSE(inputBlockRevokesFocus(INPUT_BLOCK_NONE, false));
    EXPECT_TRUE(inputBlockRevokesFocus(INPUT_BLOCK_NONE, true));
    EXPECT_TRUE(inputBlockRevokesFocus(INPUT_BLOCK_SPATIAL_OUTSIDE_VIEW, true));
}
