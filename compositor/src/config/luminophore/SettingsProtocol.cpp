#include "SettingsProtocol.hpp"
#include "GeneratedSettings.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>

static bool number(std::string_view text, auto& value) {
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    return error == std::errc{} && end == text.data() + text.size();
}

// Strings remain a single ASCII wire token, including spaces, empty text and UTF-8.
static std::string encodeString(std::string_view value) {
    if (value.empty())
        return "-";
    constexpr std::string_view digits = "0123456789abcdef";
    std::string encoded;
    encoded.reserve(value.size() * 2);
    for (unsigned char c : value) {
        encoded += digits[c >> 4];
        encoded += digits[c & 15];
    }
    return encoded;
}

static bool decodeString(std::string_view text, std::string& value) {
    if (text == "-")
        return true;
    if (text.empty() || text.size() % 2)
        return false;
    const auto digit = [](char c) -> int { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; };
    for (size_t i = 0; i < text.size(); i += 2) {
        const int hi = digit(text[i]), lo = digit(text[i + 1]);
        if (hi < 0 || lo < 0)
            return false;
        value += static_cast<char>((hi << 4) | lo);
    }
    // Reject invalid UTF-8, overlong encodings, surrogates and out-of-range codepoints.
    for (size_t i = 0; i < value.size();) {
        const unsigned char lead = value[i++];
        if (lead < 0x80)
            continue;
        const unsigned length = lead >= 0xc2 && lead <= 0xdf ? 2 : lead >= 0xe0 && lead <= 0xef ? 3 : lead >= 0xf0 && lead <= 0xf4 ? 4 : 0;
        if (!length || i + length - 1 > value.size())
            return false;
        unsigned code = lead & (0x7f >> length);
        for (unsigned j = 1; j < length; ++j) {
            const unsigned char next = value[i++];
            if ((next & 0xc0) != 0x80)
                return false;
            code = (code << 6) | (next & 0x3f);
        }
        if ((length == 3 && code < 0x800) || (length == 4 && code < 0x10000) || (code >= 0xd800 && code <= 0xdfff) || code > 0x10ffff)
            return false;
    }
    return true;
}

std::string Luminophore::Settings::validateRequest(std::string_view request) {
    const std::string invalid = "1 - invalid";
    if (request.size() > 8192 || std::ranges::any_of(request, [](unsigned char c) { return c < 32 || c > 126; }))
        return invalid;
    std::istringstream stream{std::string{request}};
    stream.imbue(std::locale::classic());
    std::string version, id, countText;
    if (!(stream >> version >> id >> countText) || version != "1" || id.size() != 32 ||
        !std::ranges::all_of(id, [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }))
        return invalid;
    const auto reject = "1 " + id + " invalid";
    size_t     count  = 0;
    if (!number(countText, count) || count > defaults().size())
        return reject;
    Snapshot candidate;
    for (size_t i = 0; i < count; ++i) {
        std::string key, type, text;
        if (!(stream >> key >> type >> text) || candidate.contains(key))
            return reject;
        Value value;
        if (type == "i") {
            int64_t parsed = 0;
            if (!number(text, parsed))
                return reject;
            value = parsed;
        } else if (type == "f") {
            double parsed = 0;
            if (!number(text, parsed) || !std::isfinite(parsed))
                return reject;
            value = parsed;
        } else if (type == "b") {
            if (text != "true" && text != "false")
                return reject;
            value = text == "true";
        } else if (type == "h") {
            std::string decoded;
            if (!decodeString(text, decoded))
                return reject;
            value = decoded;
        } else if (type == "s")
            value = text;
        else
            return reject;
        candidate.emplace(key, value);
    }
    std::string trailing;
    if (stream >> trailing || !validate(candidate).empty())
        return reject;
    auto normalized = defaults();
    for (const auto& [key, value] : candidate)
        normalized[key] = value;
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << "1 " << id << " validated " << normalized.size() << std::setprecision(17);
    for (const auto& [key, value] : normalized) {
        output << ' ' << key << ' ';
        if (const auto* v = std::get_if<int64_t>(&value))
            output << "i " << *v;
        else if (const auto* v = std::get_if<double>(&value))
            output << "f " << *v;
        else if (const auto* v = std::get_if<bool>(&value))
            output << "b " << (*v ? "true" : "false");
        else
            output << "h " << encodeString(std::get<std::string>(value));
    }
    return output.str();
}
