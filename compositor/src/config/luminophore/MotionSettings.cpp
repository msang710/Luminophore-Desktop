#include "MotionSettings.hpp"
#include <stdexcept>
using namespace Luminophore::Settings;
std::vector<SMotionNode> Luminophore::Settings::motionNodes(const Snapshot& values) {
    if (values.size() != defaults().size() || !validate(values).empty())
        throw std::invalid_argument("invalid motion settings");
    const auto               enabled = std::get<bool>(values.at("motion.enabled"));
    const auto               preset  = std::get<std::string>(values.at("motion.preset"));
    const auto               scale   = std::get<double>(values.at("motion.speed"));
    const double             speed   = preset == "fast" ? 2 : preset == "smooth" ? 5 : 3;
    const std::string        curve   = preset == "smooth" ? "easeInOutCubic" : "quick";
    std::vector<SMotionNode> nodes   = {{"global", enabled, static_cast<float>(speed * scale), curve, ""},
                                        {"windows", enabled, static_cast<float>(speed * scale), "spring:easy", "slide"},
                                        {"specialWorkspaceIn", enabled, static_cast<float>(2 * scale), curve, "slide top"},
                                        {"specialWorkspaceOut", enabled, static_cast<float>(2 * scale), curve, "slide bottom"}};
    if (!enabled)
        for (auto& node : nodes) {
            node.speed = 1;
            node.curve = "default";
            node.style = "";
        }
    return nodes;
}
