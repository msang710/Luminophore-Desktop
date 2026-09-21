#include "LuminophoreLaunchOrigin.hpp"
#include "../managers/TokenManager.hpp"
#include <algorithm>
#include <deque>

struct SDirectLaunchToken {
    std::string group;
};
static std::deque<std::string> pending;

std::string                    Luminophore::LaunchOrigin::issue(const std::string& group) {
    if (group.size() > 64 || !std::ranges::all_of(group, [](char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-'; }))
        return {};
    if (!g_pTokenManager)
        return {};
    std::erase_if(pending, [](const auto& value) { return !g_pTokenManager->getToken(value); });
    while (pending.size() >= 128) {
        g_pTokenManager->removeToken(g_pTokenManager->getToken(pending.front()));
        pending.pop_front();
    }
    auto value = g_pTokenManager->registerNewToken(SDirectLaunchToken{group}, std::chrono::seconds(30));
    pending.push_back(value);
    return value;
}

std::optional<std::string> Luminophore::LaunchOrigin::take(const std::string& value) {
    if (!g_pTokenManager || value.empty())
        return std::nullopt;
    const auto token = g_pTokenManager->getToken(value);
    if (!token)
        return std::nullopt;
    const auto context = std::any_cast<SDirectLaunchToken>(&token->m_data);
    if (!context)
        return std::nullopt;
    const auto group = context->group;
    g_pTokenManager->removeToken(token);
    std::erase(pending, value);
    return group;
}
bool Luminophore::LaunchOrigin::consume(const std::string& value) {
    return take(value).has_value();
}
