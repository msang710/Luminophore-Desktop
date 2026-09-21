#include "LuaSettingsRuntime.hpp"
#include <sstream>
#include <stdexcept>
#include <toml++/toml.hpp>

using namespace Luminophore::Settings;

static bool hex(const std::string& value, size_t length) {
    return value.size() == length && value.find_first_not_of("0123456789abcdef") == std::string::npos;
}
static std::string decode(const std::string& value) {
    if (value.size() > 2 * 1024 * 1024 || value.size() % 2 || !hex(value, value.size()))
        throw std::runtime_error("invalid payload");
    std::string result;
    for (size_t i = 0; i < value.size(); i += 2)
        result += static_cast<char>(std::stoi(value.substr(i, 2), nullptr, 16));
    return result;
}
static Snapshot snapshot(const toml::table* table, bool complete) {
    if (!table)
        throw std::runtime_error("missing snapshot");
    Snapshot result;
    for (const auto& [key, node] : *table) {
        if (node.is_boolean())
            result.emplace(key.str(), *node.value<bool>());
        else if (node.is_integer())
            result.emplace(key.str(), *node.value<int64_t>());
        else if (node.is_floating_point())
            result.emplace(key.str(), *node.value<double>());
        else if (node.is_string())
            result.emplace(key.str(), *node.value<std::string>());
        else
            throw std::runtime_error("invalid snapshot type");
    }
    if (complete && (result.size() != defaults().size() || !validate(result).empty()))
        throw std::runtime_error("invalid snapshot");
    return result;
}
static SGeneration generation(const toml::table& data, const std::string& name, const std::string& id) {
    SGeneration result{.id = id, .values = snapshot(data[name].as_table(), true)};
    if (const auto* devices = data[name + "_devices"].as_table())
        for (const auto& [key, node] : *devices)
            result.devices.emplace(key.str(), snapshot(node.as_table(), false));
    validateGenerationInputs(result);
    return result;
}
CLuaSettingsTransactions::CLuaSettingsTransactions(Factory factory) : m_factory(std::move(factory)) {}
std::string CLuaSettingsTransactions::request(std::string_view wire) {
    try {
        if (wire.size() > 2 * 1024 * 1024 + 512)
            throw std::runtime_error("oversized request");
        std::istringstream in{std::string(wire)};
        std::string        version, id, base, candidate, operation, payload, extra;
        if (!(in >> version >> id >> base >> candidate >> operation) || version != "1" || !hex(id, 32) || !hex(base, 64) || !hex(candidate, 64))
            throw std::runtime_error("invalid request");
        const auto identity = id + " " + base + " " + candidate;
        if (operation == "prepare") {
            if (!(in >> payload) || (in >> extra))
                throw std::runtime_error("invalid prepare");
            if (identity == m_identity) {
                if (payload != m_payload || m_phase != "prepared")
                    throw std::runtime_error("request replay conflict");
            } else {
                if (!m_identity.empty() && m_phase != "confirmed" && m_phase != "restored")
                    throw std::runtime_error("transaction pending");
                const auto data    = toml::parse(decode(payload));
                auto       backend = m_factory(generation(data, "before", base), generation(data, "after", candidate));
                m_backend          = std::move(backend);
                m_identity         = identity;
                m_payload          = payload;
                m_phase            = "prepared";
            }
        } else {
            if ((in >> extra) || identity != m_identity)
                throw std::runtime_error("unknown transaction");
            if (operation == "status") {
                // Identity-checked read-only recovery; never replays a mutation.
            } else if (operation == "apply") {
                if (m_phase != "prepared")
                    throw std::runtime_error("apply phase");
                m_phase = "applying";
                m_backend.apply();
                m_phase = "applied";
            } else if (operation == "verify") {
                if (m_phase != "applied" && m_phase != "verified" && m_phase != "confirmed")
                    throw std::runtime_error("verify phase");
                m_backend.verify();
                m_phase = "verified";
            } else if (operation == "restore") {
                if (m_phase == "confirmed")
                    throw std::runtime_error("already confirmed");
                m_backend.restore();
                m_phase = "restored";
            } else if (operation == "confirm") {
                if (m_phase != "verified" && m_phase != "confirmed")
                    throw std::runtime_error("confirm phase");
                m_backend.verify();
                m_phase = "confirmed";
            } else
                throw std::runtime_error("unknown operation");
        }
        return R"({"version":1,"status":"ok","request_id":")" + id + R"(","base":")" + base + R"(","candidate":")" + candidate + R"(","phase":")" + m_phase + R"("})";
    } catch (const std::exception& error) {
        std::string message;
        for (const char c : std::string(error.what()).substr(0, 512)) {
            if (c == '"' || c == '\\')
                message += '\\';
            if (static_cast<unsigned char>(c) < 32)
                message += ' ';
            else
                message += c;
        }
        return R"({"version":1,"status":"failed","error":")" + message + R"("})";
    }
}
