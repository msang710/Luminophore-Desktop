#include <state/WorkspaceQueryCore.hpp>

#include <gtest/gtest.h>

#include <optional>
#include <vector>

static State::SWorkspaceQueryable workspace(WORKSPACEID id, std::string_view name, bool special = false, bool inert = false) {
    return {
        .id      = id,
        .name    = name,
        .inert   = inert,
        .special = special,
    };
}

TEST(WorkspaceQueryCore, queryById) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
        workspace(2, "2"),
    };

    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).id(2).run(), std::optional<size_t>{1});
}

TEST(WorkspaceQueryCore, queryByName) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
        workspace(-1338, "code"),
    };

    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).name("code").run(), std::optional<size_t>{1});
}

TEST(WorkspaceQueryCore, queryByNameString) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
        workspace(-1338, "code"),
    };

    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).string("name:code").run(), std::nullopt);
}

TEST(WorkspaceQueryCore, queryByNumericString) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
        workspace(5, "5"),
    };

    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).string("5").run(), std::nullopt);
}

TEST(WorkspaceQueryCore, queryByPlainNameString) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
        workspace(-1338, "code"),
    };

    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).string("code").run(), std::nullopt);
}

TEST(WorkspaceQueryCore, queryBySpecialString) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(SPECIAL_WORKSPACE_START, "special:special", true),
        workspace(SPECIAL_WORKSPACE_START + 1, "special:luminophore-spotify", true),
    };

    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).string("special").run(), std::nullopt);
    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).string("special:term").run(), std::nullopt);
}

TEST(WorkspaceQueryCore, internalServiceSelectorRemainsAvailable) {
    std::vector<State::SWorkspaceQueryable> workspaces = {workspace(SPECIAL_WORKSPACE_START + 1, "special:luminophore-spotify", true)};
    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).string("special:luminophore-spotify").run(), std::optional<size_t>{0});
}

TEST(WorkspaceQueryCore, inertWorkspacesAreIgnored) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "stale", false, true),
        workspace(1, "active"),
    };

    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{workspaces}).id(1).run(), std::optional<size_t>{1});
}

TEST(WorkspaceQueryCore, invalidLookupReturnsNullopt) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
    };

    EXPECT_FALSE(std::move(State::CWorkspaceQueryCore{workspaces}).id(2).run().has_value());
    EXPECT_FALSE(std::move(State::CWorkspaceQueryCore{workspaces}).string("2").run().has_value());
}

TEST(WorkspaceQueryCore, specialIdsMatchWorkspaceRange) {
    EXPECT_FALSE(State::CWorkspaceQueryCore::isSpecial(1));
    EXPECT_FALSE(State::CWorkspaceQueryCore::isSpecial(-1));
    EXPECT_TRUE(State::CWorkspaceQueryCore::isSpecial(SPECIAL_WORKSPACE_START));
    EXPECT_TRUE(State::CWorkspaceQueryCore::isSpecial(SPECIAL_WORKSPACE_START + 1));
}

TEST(WorkspaceQueryCore, newSpecialIDUsesHighestExistingSpecial) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
        workspace(SPECIAL_WORKSPACE_START, "special:special", true),
        workspace(SPECIAL_WORKSPACE_START + 2, "special:term", true),
        workspace(SPECIAL_WORKSPACE_START + 3, "special:stale", true, true),
    };

    EXPECT_EQ(State::CWorkspaceQueryCore::newSpecialID(workspaces), SPECIAL_WORKSPACE_START + 3);
}

TEST(WorkspaceQueryCore, newSpecialIDUsesStartWhenNoneExist) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
    };

    EXPECT_EQ(State::CWorkspaceQueryCore::newSpecialID(workspaces), SPECIAL_WORKSPACE_START + 1);
}

TEST(WorkspaceQueryCore, nextAvailableNamedWorkspaceUsesExistingNamedWorkspaces) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(1, "1"),
        workspace(-1338, "code"),
        workspace(-1340, "chat"),
    };

    EXPECT_EQ(State::CWorkspaceQueryCore::nextAvailableNamedWorkspace(workspaces), -1341);
}

TEST(WorkspaceQueryCore, nextAvailableNamedWorkspaceIncludesPersistentRules) {
    std::vector<State::SWorkspaceQueryable> workspaces = {
        workspace(-1338, "code"),
    };
    std::vector<WORKSPACEID> persistentWorkspaceIDs = {-1345};

    EXPECT_EQ(State::CWorkspaceQueryCore::nextAvailableNamedWorkspace(workspaces, persistentWorkspaceIDs), -1346);
}

TEST(WorkspaceQueryCore, BaseIdentityAndRetiredSelectors) {
    std::vector<State::SWorkspaceQueryable> spaces = {workspace(-1338, "luminophore-base-DP-1")};
    EXPECT_EQ(std::move(State::CWorkspaceQueryCore{spaces}).string("name:luminophore-base-DP-1").run(), std::optional<size_t>{0});
    for (const auto selector : {"1", "name:code", "code", "previous", "previous_per_monitor", "empty", "r+1", "m-1", "+1", "name:luminophore-base-"})
        EXPECT_EQ(std::move(State::CWorkspaceQueryCore{spaces}).string(selector).run(), std::nullopt) << selector;
}
