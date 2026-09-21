#pragma once
#include <string>
#include <vector>
namespace Luminophore::Settings {
    struct SInputCurve {
        double              step = 0;
        std::vector<double> points;
    };
    SInputCurve parseInputCurve(const std::string& text, bool profile);
}
