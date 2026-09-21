#include "../../src/luminophore/LuminophoreSpatialRuntime.hpp"

#include <gtest/gtest.h>

using namespace Luminophore;

TEST(LuminophoreSpatialParticipation, TopLevelWindowsKeepIndependentCoordinatesDespiteParentMetadata) {
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .hasWorkspace = true}), eSpatialParticipation::BOARD_ROOT);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = false, .hasWorkspace = true}), eSpatialParticipation::ABSENT);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true}), eSpatialParticipation::ABSENT);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .hasWorkspace = true, .hasParent = true}), eSpatialParticipation::BOARD_ROOT);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .hasWorkspace = true, .modal = true}), eSpatialParticipation::BOARD_ROOT);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .hasWorkspace = true, .pinned = true}), eSpatialParticipation::EXTERNAL_OVERLAY);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .hasWorkspace = true, .workspaceOverlay = true}), eSpatialParticipation::EXTERNAL_OVERLAY);
}

TEST(LuminophoreSpatialParticipation, NativeAuxiliaryWithoutParentIsAnExternalOverlay) {
    const SSpatialParticipationFacts notification{.mapped = true, .hasWorkspace = true, .nativeAuxiliary = true};
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation(notification), eSpatialParticipation::EXTERNAL_OVERLAY);
    auto ordinary            = notification;
    ordinary.nativeAuxiliary = false;
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation(ordinary), eSpatialParticipation::BOARD_ROOT);
    auto closed   = notification;
    closed.mapped = false;
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation(closed), eSpatialParticipation::ABSENT);
}

TEST(LuminophoreSpatialParticipation, NativeAuxiliaryStaysOutsideEvenWithParentMetadata) {
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .hasWorkspace = true, .hasParent = true, .nativeAuxiliary = true}),
              eSpatialParticipation::EXTERNAL_OVERLAY);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .hasWorkspace = true, .modal = true, .nativeAuxiliary = true}), eSpatialParticipation::EXTERNAL_OVERLAY);
    EXPECT_EQ(CLuminophoreSpatialRuntime::classifyParticipation({.mapped = true, .nativeAuxiliary = true}), eSpatialParticipation::ABSENT);
}
