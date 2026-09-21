#pragma once
#include "GeneratedSettings.hpp"
#include <vector>
namespace Luminophore::Settings {
    struct SMotionNode {
        std::string name;
        bool        enabled = true;
        float       speed   = 1;
        std::string curve;
        std::string style;
    };
    std::vector<SMotionNode> motionNodes(const Snapshot& values);
}
