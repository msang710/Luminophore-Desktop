#pragma once
#include "JointSettings.hpp"
#include "SettingsParticipant.hpp"
#include <memory>

namespace Luminophore::Settings {
    // load must read the immutable, hash-validated generation, never live files.
    SSettingsMember makeJointMember(std::shared_ptr<CSettingsParticipant> participant, std::function<Snapshot(const std::string&)> load);
}
