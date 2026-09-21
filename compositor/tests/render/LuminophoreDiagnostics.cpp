#include <gtest/gtest.h>

#include "../../src/debug/LuminophoreDiagnostics.hpp"
#include "../../src/helpers/math/Math.hpp"

using namespace Render;

TEST(LuminophoreDiagnostics, AssociatesPresentationWithLatestMonitorRequest) {
    CLuminophoreDiagnostics diagnostics;
    const auto       sequence = diagnostics.recordFrameRequest(7, LUMINOPHORE_FRAME_WINDOW_GLOW_ACTIVE, CBox{0, 0, 20, 10});

    diagnostics.recordPresented(7);
    const auto events = diagnostics.frameEvents();

    ASSERT_EQ(events.size(), 1);
    EXPECT_EQ(events.front().sequence, sequence);
    EXPECT_EQ(events.front().monitor, 7);
    EXPECT_EQ(events.front().damageArea, 200);
    EXPECT_GT(events.front().presentedAtUs, 0);
}

TEST(LuminophoreDiagnostics, PresentationDoesNotAcknowledgeAnotherMonitor) {
    CLuminophoreDiagnostics diagnostics;
    diagnostics.recordFrameRequest(1, LUMINOPHORE_FRAME_WINDOW_GLOW_ACTIVE, CBox{0, 0, 4, 4});
    diagnostics.recordFrameRequest(2, LUMINOPHORE_FRAME_SHELL_TRANSITION, CBox{0, 0, 8, 8});

    diagnostics.recordPresented(1);
    const auto events = diagnostics.frameEvents();

    ASSERT_EQ(events.size(), 2);
    EXPECT_GT(events[0].presentedAtUs, 0);
    EXPECT_EQ(events[1].presentedAtUs, 0);
}

TEST(LuminophoreDiagnostics, RetainsBoundedRecentHistory) {
    CLuminophoreDiagnostics diagnostics;
    for (size_t i = 0; i < 300; ++i)
        diagnostics.recordFrameRequest(3, LUMINOPHORE_FRAME_WINDOW_GLOW_ACTIVE, CBox{0, 0, 1, 1});

    const auto events = diagnostics.frameEvents();
    ASSERT_EQ(events.size(), 256);
    EXPECT_EQ(events.front().sequence, 45);
    EXPECT_EQ(events.back().sequence, 300);
}
