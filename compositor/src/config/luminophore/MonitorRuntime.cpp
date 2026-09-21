#include "MonitorRuntime.hpp"
#include "../shared/monitor/MonitorRuleManager.hpp"
#include "../../state/MonitorState.hpp"
#include "../../output/Monitor.hpp"
#include "../../event/EventBus.hpp"
#include <cmath>
#include <memory>
#include <stdexcept>

using namespace Luminophore::Settings;

static Config::CMonitorRule ruleFor(const std::string& name, const SMonitorSettings& value) {
    Config::CMonitorRule rule;
    rule.m_name        = name;
    rule.m_resolution  = {value.width, value.height};
    rule.m_refreshRate = value.refresh;
    rule.m_scale       = value.scale;
    rule.m_transform   = static_cast<wl_output_transform>(value.transform);
    rule.m_disabled    = value.disabled;
    rule.m_vrr         = value.vrr;
    if (value.position)
        rule.m_offset = {value.position->first, value.position->second};
    return rule;
}
static void install(const std::vector<Config::CMonitorRule>& rules) {
    Config::monitorRuleMgr()->clear();
    for (const auto& rule : rules)
        Config::monitorRuleMgr()->add(Config::CMonitorRule{rule});
    if (!State::monitorState()->allMonitors().empty()) {
        Config::monitorRuleMgr()->ensureMonitorStatus();
        Config::monitorRuleMgr()->ensureVRR();
    }
}
static std::map<std::string, uintptr_t> topology() {
    std::map<std::string, uintptr_t> result;
    for (const auto& mon : State::monitorState()->allMonitors())
        if (mon && mon->m_output && !mon->m_isUnsafeFallback)
            result.emplace(mon->m_name, reinterpret_cast<uintptr_t>(mon->m_output.get()));
    return result;
}
static void verifyRules(const MonitorSettings& expected) {
    for (const auto& mon : State::monitorState()->allMonitors()) {
        const auto found = expected.find(mon->m_name);
        if (found == expected.end())
            continue;
        const auto& value = found->second;
        if (mon->m_activeMonitorRule.m_vrr != value.vrr)
            throw std::runtime_error("monitor VRR policy mismatch: " + mon->m_name);
        if (mon->enabled() == value.disabled)
            throw std::runtime_error("monitor enabled state mismatch");
        if (value.disabled)
            continue;
        if ((value.width && (mon->m_pixelSize.x != value.width || mon->m_pixelSize.y != value.height)) || (value.scale > 0 && std::abs(mon->m_scale - value.scale) > .001) ||
            mon->m_transform != value.transform || (value.position && (mon->m_position.x != value.position->first || mon->m_position.y != value.position->second)) ||
            (value.width && mon->m_refreshRate > 0 && std::abs(mon->m_refreshRate - value.refresh) > 1.0))
            throw std::runtime_error("monitor effective state mismatch: " + mon->m_name);
    }
}
struct SMonitorTransaction {
    uint64_t            topologyRevision = 0, preparedRevision = 0;
    CHyprSignalListener added, removed;
    SMonitorTransaction() {
        added   = Event::bus()->m_events.monitor.newMon.listen([this](PHLMONITOR) { ++topologyRevision; });
        removed = Event::bus()->m_events.monitor.destroyMon.listen([this](PHLMONITOR) { ++topologyRevision; });
    }

    MonitorSettings                   candidate, baseline;
    std::vector<Config::CMonitorRule> restoreRules;
    std::map<std::string, uintptr_t>  outputs;
    bool                              prepared = false;
    void                              prepare(const MonitorSettings& values) {
        outputs          = topology();
        preparedRevision = topologyRevision;
        if (outputs.empty())
            throw std::runtime_error("no usable monitor");
        candidate = values;
        baseline.clear();
        restoreRules     = Config::monitorRuleMgr()->all();
        unsigned visible = 0;
        for (const auto& mon : State::monitorState()->allMonitors()) {
            if (!outputs.contains(mon->m_name))
                continue;
            const auto it = values.find(mon->m_name);
            if (it == values.end() ? mon->enabled() : !it->second.disabled)
                ++visible;
            SMonitorSettings state;
            state.width     = mon->m_pixelSize.x;
            state.height    = mon->m_pixelSize.y;
            state.refresh   = mon->m_refreshRate;
            state.scale     = mon->m_scale;
            state.transform = mon->m_transform;
            state.position  = {{static_cast<int>(mon->m_position.x), static_cast<int>(mon->m_position.y)}};
            state.disabled  = !mon->enabled();
            state.vrr       = mon->m_activeMonitorRule.m_vrr;
            baseline.emplace(mon->m_name, state);
            auto restore         = Config::monitorRuleMgr()->get(mon);
            restore.m_name       = mon->m_name;
            restore.m_resolution = mon->m_pixelSize;
            restore.m_scale      = mon->m_scale;
            restore.m_transform  = mon->m_transform;
            restore.m_offset     = mon->m_position;
            restore.m_disabled   = state.disabled;
            if (mon->m_refreshRate > 0)
                restore.m_refreshRate = mon->m_refreshRate;
            std::erase_if(restoreRules, [&](const auto& r) { return r.m_name == mon->m_name; });
            restoreRules.push_back(restore);
        }
        if (!visible)
            throw std::runtime_error("cannot disable all connected monitors");
        prepared = true;
    }
    bool healthy() const {
        return prepared && preparedRevision == topologyRevision && outputs == topology();
    }
    void apply() {
        if (!healthy())
            throw std::runtime_error("monitor topology changed");
        std::vector<Config::CMonitorRule> rules;
        for (const auto& [name, value] : candidate)
            rules.push_back(ruleFor(name, value));
        install(rules);
        verify();
    }
    void verify() const {
        if (!healthy())
            throw std::runtime_error("monitor topology changed");
        verifyRules(candidate);
    }
    void restore() {
        if (!prepared)
            return;
        install(restoreRules);
        verifyRules(baseline);
        if (!healthy())
            throw std::runtime_error("monitor topology changed during restore");
    }
};
SMonitorRuntime Luminophore::Settings::makeMonitorRuntime(const MonitorSettings& boot) {
    std::vector<Config::CMonitorRule> rules;
    for (const auto& [name, value] : boot)
        rules.push_back(ruleFor(name, value));
    install(rules);
    auto state = std::make_shared<SMonitorTransaction>();
    return {[state](const MonitorSettings& value) { state->prepare(value); }, [state] { state->apply(); }, [state] { state->verify(); }, [state] { state->restore(); },
            [state] { return state->healthy(); }};
}
