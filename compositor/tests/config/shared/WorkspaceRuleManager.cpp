#include <config/shared/workspace/WorkspaceRuleManager.hpp>
#include <output/IMonitorIdentifiable.hpp>

#include <gtest/gtest.h>
#include <hyprutils/string/String.hpp>

#include <string>

static Config::CWorkspaceRule workspaceRule(const std::string& workspace, const std::string& monitor) {
    Config::CWorkspaceRule rule;
    rule.m_workspaceString = workspace;
    rule.m_workspaceName   = workspace;
    rule.m_monitor         = monitor;
    return rule;
}

TEST(WorkspaceRuleManager, disabledMonitorBindingIsSkipped) {
    Config::CWorkspaceRuleManager manager;
    auto                          rule = manager.add(workspaceRule("4", "DP-1"));
    rule->setEnabled(false);

    EXPECT_EQ(manager.getBoundMonitorStringForWS("4"), "");
}

TEST(WorkspaceRuleManager, replaceOrAddKeepsExistingSharedRule) {
    Config::CWorkspaceRule first = workspaceRule("4", "DP-1");
    first.m_isPersistent         = true;

    Config::CWorkspaceRule second = workspaceRule("4", "DP-2");
    second.m_isPersistent         = false;

    Config::CWorkspaceRuleManager manager;
    const auto                    firstPtr  = manager.replaceOrAdd(std::move(first));
    const auto                    secondPtr = manager.replaceOrAdd(std::move(second));

    EXPECT_EQ(firstPtr, secondPtr);
    EXPECT_EQ(firstPtr->m_monitor, "DP-2");
    EXPECT_FALSE(firstPtr->m_isPersistent.value_or(true));
}
