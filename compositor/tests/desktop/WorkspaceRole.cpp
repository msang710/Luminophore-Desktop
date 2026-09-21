#include <desktop/Workspace.hpp>
#include <helpers/MiscFunctions.hpp>

#include <gtest/gtest.h>

TEST(WorkspaceRole, ClassifiesBaseDesktopByStableName) {
    EXPECT_EQ(CWorkspace::roleFor("luminophore-base-DP-2", false), WORKSPACE_ROLE_BASE);
}

TEST(WorkspaceRole, OnlyServiceHasHiddenSpaceRole) {
    EXPECT_EQ(CWorkspace::roleFor("special:special", true), WORKSPACE_ROLE_UNKNOWN);
    EXPECT_EQ(CWorkspace::roleFor("special:luminophore-spotify", true), WORKSPACE_ROLE_SERVICE);
}

TEST(WorkspaceRole, RejectsLegacyWorkspaceIdentities) {
    EXPECT_EQ(CWorkspace::roleFor("1", false), WORKSPACE_ROLE_UNKNOWN);
    EXPECT_EQ(CWorkspace::roleFor("code", false), WORKSPACE_ROLE_UNKNOWN);
}

TEST(WorkspaceRole, RemovedSpecialSelectorsCannotCreateSpaces) {
    for (const auto* selector : {"special", "special:special", "special:term", "special:", "special:luminophore-spotify-extra"})
        EXPECT_EQ(getWorkspaceIDNameFromString(selector).id, WORKSPACE_INVALID);
}

TEST(WorkspaceRole, RetiredNavigationSelectorsNeverAllocate) {
    for (const auto* selector : {"1", "name:code", "previous", "previous_per_monitor", "empty", "r+1", "m-1", "+1", "code", "name:luminophore-base-"})
        EXPECT_EQ(getWorkspaceIDNameFromString(selector).id, WORKSPACE_INVALID) << selector;
}

TEST(WorkspaceRole, EmptyBaseIdentityIsRejected) {
    EXPECT_EQ(CWorkspace::roleFor("luminophore-base-", false), WORKSPACE_ROLE_UNKNOWN);
}
