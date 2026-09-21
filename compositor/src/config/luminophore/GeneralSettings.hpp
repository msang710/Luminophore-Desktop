#pragma once
#include "../shared/complex/ComplexDataTypes.hpp"
#include <string>

namespace Luminophore::Settings {
    int64_t                    parseGeneralColor(const std::string& value);
    std::string                generalColor(int64_t value);
    Config::CGradientValueData parseShadowGradient(const std::string& value);
    std::string                shadowGradient(const Config::CGradientValueData& value);
}
