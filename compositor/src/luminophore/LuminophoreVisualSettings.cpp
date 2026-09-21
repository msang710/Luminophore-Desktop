#include "LuminophoreVisualSettings.hpp"

#include <algorithm>
#include <cmath>
#include <utility>
#include <format>
#include <limits>
#include <stdexcept>
#include <random>
#include <charconv>
#include <sstream>
#include <vector>

using namespace Luminophore;

std::optional<SVisualCommand> Luminophore::parseVisualCommand(std::string_view wire) {
    if (wire.size() > 1024)
        return std::nullopt;
    std::istringstream       input{std::string(wire)};
    std::vector<std::string> words;
    for (std::string word; input >> word;)
        words.push_back(std::move(word));
    if (words.size() != 5 && words.size() != 8)
        return std::nullopt;
    const auto number = [](const std::string& word, auto& value) {
        const auto [end, error] = std::from_chars(word.data(), word.data() + word.size(), value);
        return error == std::errc{} && end == word.data() + word.size();
    };
    const auto token = [](const std::string& word, size_t limit, std::string_view chars) {
        return !word.empty() && word.size() <= limit && word.find_first_not_of(chars) == std::string::npos;
    };
    SVisualCommand result;
    if (!number(words[0], result.settings.schemaVersion) || !number(words[4], result.settings.intensity) || !std::isfinite(result.settings.intensity) ||
        !token(words[1], 256, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") || (words[2] != "true" && words[2] != "false") ||
        (words[3] != "true" && words[3] != "false"))
        return std::nullopt;
    result.settings.preset    = words[1];
    result.settings.enabled   = words[2] == "true";
    result.settings.breathing = words[3] == "true";
    if (words.size() == 8) {
        SVisualReceiptRequest receipt{.source = words[5], .token = words[6]};
        if (words[5].size() != 32 || !token(words[5], 32, "0123456789abcdef") || !token(words[6], 128, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") ||
            !number(words[7], receipt.serial))
            return std::nullopt;
        result.receipt = std::move(receipt);
    }
    return result;
}

CLuminophoreVisualSettings::CLuminophoreVisualSettings() {
    std::random_device random;
    m_source = std::format("{:08x}{:08x}{:08x}{:08x}", random(), random(), random(), random());
}

const std::string& CLuminophoreVisualSettings::source() const {
    return m_source;
}

uint64_t CLuminophoreVisualSettings::serial() const {
    return m_serial;
}

SVisualResolution CLuminophoreVisualSettings::resolve(const std::optional<SVisualSettings>& request) {
    SVisualResolution result;
    if (!request)
        result.recovery = eVisualRecovery::MALFORMED;
    else if (request->schemaVersion != 1)
        result.recovery = eVisualRecovery::SCHEMA;
    else if (request->preset != "balanced")
        result.recovery = eVisualRecovery::PRESET;
    else if (!std::isfinite(request->intensity))
        result.recovery = eVisualRecovery::INTENSITY;
    else {
        result.bundle.settings           = *request;
        result.bundle.settings.intensity = std::clamp(request->intensity, 0.0, 3.0);
    }
    // Recovery replaces every field; values from an invalid request never leak.
    const auto& settings        = result.bundle.settings;
    result.bundle.blurEnabled   = settings.enabled;
    result.bundle.glowIntensity = settings.enabled ? settings.intensity : 0.0;
    result.bundle.animated      = settings.enabled && settings.breathing && settings.intensity > 0.0;
    return result;
}

const SVisualResolution& CLuminophoreVisualSettings::apply(const std::optional<SVisualSettings>& request, const std::optional<SVisualReceiptRequest>& receipt) {
    if (receipt &&
        (receipt->source != m_source || receipt->serial != m_serial || receipt->token.empty() || receipt->token.size() > 128 ||
         receipt->token.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_") != std::string::npos))
        throw std::invalid_argument("stale or invalid visual receipt request");
    if (m_serial == std::numeric_limits<uint64_t>::max())
        throw std::overflow_error("visual receipt serial exhausted");
    auto       token     = receipt ? receipt->token : std::string{};
    auto       candidate = resolve(request);
    const bool changed   = !m_configured || candidate.bundle != m_current.bundle;
    if (changed && m_revision == std::numeric_limits<uint64_t>::max())
        throw std::overflow_error("visual settings revision exhausted");
    m_current = std::move(candidate);
    if (changed)
        ++m_revision;
    m_configured = true;
    m_token      = std::move(token);
    ++m_serial;
    return m_current;
}

const SVisualResolution& CLuminophoreVisualSettings::current() const {
    return m_current;
}

static double fract(double value) {
    return value - std::floor(value);
}

double CLuminophoreVisualSettings::gainAt(const SVisualBundle& bundle, double seconds, double phase) {
    if (!bundle.settings.enabled || bundle.glowIntensity <= 0.0)
        return 0.0;
    if (!bundle.animated || !std::isfinite(seconds) || !std::isfinite(phase))
        return bundle.glowIntensity;
    const double period     = bundle.minimumPeriod + (bundle.maximumPeriod - bundle.minimumPeriod) * fract(phase * 2.73 + 0.17);
    const double cycle      = fract(seconds / period + phase);
    const double character  = fract(phase * 4.17 + 0.31);
    const double rise       = 0.32 + (0.44 - 0.32) * character;
    const auto   smoothstep = [](double left, double right, double value) {
        const double x = std::clamp((value - left) / (right - left), 0.0, 1.0);
        return x * x * (3.0 - 2.0 * x);
    };
    const double breath = smoothstep(0.0, rise, cycle) * (1.0 - smoothstep(rise, 1.0, cycle));
    return (bundle.minimumGain + (bundle.maximumGain - bundle.minimumGain) * breath) * bundle.glowIntensity;
}

UP<CLuminophoreVisualSettings>& Luminophore::visualSettings() {
    static auto settings = makeUnique<CLuminophoreVisualSettings>();
    return settings;
}

uint64_t CLuminophoreVisualSettings::revision() const {
    return m_revision;
}

bool CLuminophoreVisualSettings::configured() const {
    return m_configured;
}

std::string CLuminophoreVisualSettings::json() const {
    const char* recovery = "none";
    switch (m_current.recovery) {
        case eVisualRecovery::MALFORMED: recovery = "malformed"; break;
        case eVisualRecovery::SCHEMA: recovery = "schema"; break;
        case eVisualRecovery::PRESET: recovery = "preset"; break;
        case eVisualRecovery::INTENSITY: recovery = "intensity"; break;
        default: break;
    }
#ifdef LUMINOPHORE_EFFECTS
    constexpr int RENDERER_SCHEMA = 1;
#else
    constexpr int RENDERER_SCHEMA = 0;
#endif
    const auto& settings = m_current.bundle.settings;
    // Resolved presets are closed, validated identifiers, never caller text.
    return std::format(
        R"({{"schema":1,"rendererSchema":{},"receiptSchema":1,"source":"{}","serial":"{}","token":"{}","revision":"{}","configured":{},"recovery":"{}","settings":{{"schemaVersion":{},"preset":"{}","enabled":{},"breathing":{},"intensity":{}}}}})",
        RENDERER_SCHEMA, m_source, m_serial, m_token, m_revision, m_configured, recovery, settings.schemaVersion, settings.preset, settings.enabled, settings.breathing,
        settings.intensity);
}

Render::SBlurParameters SVisualBundle::blurParameters() const {
    return {.size = blurSize, .passes = blurPasses};
}
