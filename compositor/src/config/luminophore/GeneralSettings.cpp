#include "GeneralSettings.hpp"
#include <charconv>
#include <cmath>
#include <format>
#include <sstream>
#include <stdexcept>

using namespace Luminophore::Settings;

int64_t Luminophore::Settings::parseGeneralColor(const std::string& value) {
    uint32_t   color  = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), color, 16);
    if (value.size() != 8 || parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size())
        throw std::invalid_argument("invalid AARRGGBB color");
    return color;
}

std::string Luminophore::Settings::generalColor(int64_t value) {
    return std::format("{:08x}", static_cast<uint32_t>(value));
}

Config::CGradientValueData Luminophore::Settings::parseShadowGradient(const std::string& value) {
    if (value == "inherit")
        return Config::CGradientValueData{CHyprColor{-1}};
    std::istringstream      input(value);
    std::string             token;
    std::vector<CHyprColor> colors;
    while (input >> token) {
        if (token.ends_with("deg")) {
            token.resize(token.size() - 3);
            int        degrees = 0;
            const auto parsed  = std::from_chars(token.data(), token.data() + token.size(), degrees);
            if (parsed.ec != std::errc{} || parsed.ptr != token.data() + token.size() || degrees < 0 || degrees >= 360 || colors.empty())
                throw std::invalid_argument("invalid shadow gradient angle");
            if (input >> token)
                throw std::invalid_argument("trailing shadow gradient data");
            return {std::move(colors), static_cast<float>(degrees * M_PI / 180.0)};
        }
        if (colors.size() >= 10)
            throw std::invalid_argument("too many shadow colors");
        colors.emplace_back(parseGeneralColor(token));
    }
    throw std::invalid_argument("missing shadow gradient angle");
}

std::string Luminophore::Settings::shadowGradient(const Config::CGradientValueData& value) {
    std::string result;
    for (const auto& color : value.m_colors)
        result += generalColor(color.getAsHex()) + " ";
    return result + std::to_string(std::lround(value.m_angle * 180.0 / M_PI)) + "deg";
}
