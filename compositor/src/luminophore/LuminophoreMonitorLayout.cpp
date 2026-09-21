#include "LuminophoreMonitorLayout.hpp"
#include "LuminophoreSpatialRuntime.hpp"
#include "../render/Renderer.hpp"
#include "../pointer/PointerManager.hpp"
#include "../output/Monitor.hpp"
#include "../state/MonitorState.hpp"
#include "../state/MonitorLayoutController.hpp"
#include "../config/ConfigValue.hpp"
#include "../helpers/MiscFunctions.hpp"
#include "../managers/eventLoop/EventLoopManager.hpp"
#include "../managers/eventLoop/EventLoopTimer.hpp"
#include <sstream>
#include <chrono>
#include <algorithm>
#include <cmath>
#include <set>

using namespace Luminophore::MonitorLayout;
bool SPreviewLease::pending() const {
    return phase == "preview" || phase == "confirming";
}
bool SPreviewLease::expired(std::chrono::steady_clock::time_point now, const std::string& currentTopology) const {
    return pending() && (now >= deadline || topology != currentTopology);
}
static bool inApply = false;
bool        Luminophore::MonitorLayout::applying() {
    return inApply;
}

std::optional<Positions> Luminophore::MonitorLayout::parse(const std::string& text) {
    if (text.size() > 16384)
        return std::nullopt;
    Positions          result;
    std::istringstream stream(text);
    std::string        key;
    int                x = 0, y = 0;
    while (stream >> key) {
        if (key.size() > 1024 || !std::ranges::all_of(key, [](unsigned char c) { return std::isalnum(c) || c == '-' || c == '_'; }) || !(stream >> x >> y) ||
            std::abs(static_cast<int64_t>(x)) > 100000 || std::abs(static_cast<int64_t>(y)) > 100000 || result.contains(key) || result.size() >= 32)
            return std::nullopt;
        result[key] = {x, y};
    }
    return result;
}

bool Luminophore::MonitorLayout::validate(const std::vector<SOutput>& outputs, const Positions& positions) {
    if (outputs.empty() || outputs.size() != positions.size() || outputs.size() > 32)
        return false;
    std::vector<CBox> boxes;
    for (const auto& o : outputs) {
        const auto p = positions.find(o.key);
        if (p == positions.end() || o.width <= 0 || o.height <= 0 || !std::isfinite(p->second.x) || !std::isfinite(p->second.y) || std::abs(p->second.x) > 100000 ||
            std::abs(p->second.y) > 100000)
            return false;
        CBox b{p->second.x, p->second.y, static_cast<double>(o.width), static_cast<double>(o.height)};
        for (const auto& previous : boxes)
            if (b.x < previous.x + previous.w && b.x + b.w > previous.x && b.y < previous.y + previous.h && b.y + b.h > previous.y)
                return false;
        boxes.push_back(b);
    }
    return true;
}

static std::string hex(const std::string& text) {
    std::string out;
    for (unsigned char c : text) {
        out += "0123456789abcdef"[c >> 4];
        out += "0123456789abcdef"[c & 15];
    }
    return out;
}
static std::string keyFor(PHLMONITOR m) {
    const auto& output = m->m_output;
    if (output && !output->serial.empty()) {
        int count = 1;
        for (const auto& n : State::monitorState()->monitors())
            if (n != m && n->m_output && n->m_output->serial == output->serial && n->m_output->make == output->make && n->m_output->model == output->model)
                ++count;
        if (count == 1)
            return "s-" + hex(output->make + "\n" + output->model + "\n" + output->serial);
    }
    return "c-" + hex(m->m_name);
}
static std::vector<SOutput> outputs() {
    std::vector<SOutput> result;
    for (const auto& m : State::monitorState()->monitors())
        if (m->m_output && m->m_enabled && !m->m_isUnsafeFallback)
            result.push_back({keyFor(m), static_cast<int>(m->m_position.x), static_cast<int>(m->m_position.y), static_cast<int>(m->m_size.x), static_cast<int>(m->m_size.y)});
    std::ranges::sort(result, {}, &SOutput::key);
    return result;
}
static std::string fingerprint() {
    std::ostringstream out;
    for (const auto& m : State::monitorState()->monitors())
        out << reinterpret_cast<uintptr_t>(m.get()) << ':' << m->m_name << ':' << m->m_size.x << ':' << m->m_size.y << ':' << m->m_scale << ':' << static_cast<int>(m->m_transform)
            << ';';
    return hex(out.str());
}
static std::string revision() {
    std::ostringstream out;
    out << fingerprint();
    for (const auto& o : outputs())
        out << ':' << o.key << ':' << o.x << ':' << o.y;
    return hex(out.str());
}
static bool supported() {
    static auto force = CConfigValue<Config::INTEGER>("xwayland:force_zero_scaling");
    float       scale = 0;
    for (const auto& m : State::monitorState()->monitors()) {
        if (!m->m_output || m->m_mirrorOf || !m->m_enabled)
            return false;
        if (*force && scale && scale != m->m_scale)
            return false;
        scale = m->m_scale;
    }
    return scale > 0;
}
static Positions             committed, priorCommitted;
static Positions             before, proposed, offsets;
static SPreviewLease         lease;
static auto&                 currentID = lease.id;
static auto&                 topology  = lease.topology;
static auto&                 phase     = lease.phase;
static std::set<std::string> used;
static SP<CEventLoopTimer>   timer;
static auto&                 deadline = lease.deadline;
static bool                  pending() {
    return phase == "preview" || phase == "confirming";
}
static bool apply(const Positions& positions) {
    inApply = true;
    struct SReset {
        ~SReset() {
            inApply = false;
        }
    } reset;
    for (const auto& m : State::monitorState()->monitors()) {
        const auto it = positions.find(keyFor(m));
        if (it != positions.end())
            m->m_activeMonitorRule.m_offset = it->second;
    }
    State::monitorLayoutController()->arrange();
    for (const auto& m : State::monitorState()->monitors()) {
        if (g_pHyprRenderer)
            g_pHyprRenderer->arrangeLayersForMonitor(m->m_id);
        m->scheduleFrame(Aquamarine::IOutput::AQ_SCHEDULE_DAMAGE);
    }
    if (Pointer::mgr())
        Pointer::mgr()->warpTo(Pointer::mgr()->position());
    if (Luminophore::spatialRuntime() && Luminophore::spatialRuntime()->active() && !Luminophore::spatialRuntime()->snapshot().committed)
        return false;
    for (const auto& o : outputs()) {
        const auto p = positions.find(o.key);
        if (p != positions.end() && p->second != Vector2D{o.x, o.y})
            return false;
    }
    return true;
}
static void rollback() {
    const bool same     = topology == fingerprint();
    const bool restored = apply(before);
    // Restore auto-position semantics after placing surviving outputs safely.
    for (const auto& m : State::monitorState()->monitors()) {
        if (auto it = offsets.find(keyFor(m)); it != offsets.end())
            m->m_activeMonitorRule.m_offset = it->second;
    }
    committed = priorCommitted;
    phase     = restored ? (same ? "rolled_back" : "topology_changed") : "recovery_failed";
}
Vector2D Luminophore::MonitorLayout::configuredPosition(PHLMONITOR monitor, Vector2D fallback) {
    static auto config = CConfigValue<Config::STRING>("misc:luminophore_monitor_layout");
    const auto  parsed = parse(*config);
    const auto& values = committed.empty() ? (parsed ? *parsed : committed) : committed;
    const auto  found  = values.find(keyFor(monitor));
    return found == values.end() ? fallback : found->second;
}
void Luminophore::MonitorLayout::configReload() {
    if (pending())
        rollback();
    committed.clear();
}
std::string Luminophore::MonitorLayout::request(const std::string& text) {
    if (lease.expired(std::chrono::steady_clock::now(), fingerprint()))
        rollback();
    std::istringstream input(text);
    std::string        command, id, expected;
    input >> command;
    if (command == "preview") {
        input >> id >> expected;
        std::string remaining;
        std::getline(input, remaining);
        const auto candidate = parse(remaining);
        if (id.empty() || id.size() > 64 || !std::ranges::all_of(id, [](char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-'; }))
            return "invalid_id";
        if (id == currentID)
            return phase;
        if (pending())
            return "busy";
        if (used.contains(id) || used.size() >= 4096)
            return "stale_request";
        if (!supported())
            return "unsupported_layout";
        if (expected != revision())
            return "stale_topology";
        if (!candidate || !validate(outputs(), *candidate))
            return "invalid_layout";
        priorCommitted = committed;
        before.clear();
        offsets.clear();
        for (const auto& m : State::monitorState()->monitors()) {
            before[keyFor(m)]  = m->m_position;
            offsets[keyFor(m)] = m->m_activeMonitorRule.m_offset;
        }
        proposed  = *candidate;
        topology  = fingerprint();
        currentID = id;
        used.insert(id);
        deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
        phase    = "preview";
        if (!timer) {
            timer = makeShared<CEventLoopTimer>(
                std::chrono::milliseconds(100),
                [](SP<CEventLoopTimer> self, void*) {
                    if (lease.expired(std::chrono::steady_clock::now(), fingerprint()))
                        rollback();
                    if (pending())
                        self->updateTimeout(std::chrono::milliseconds(100));
                },
                nullptr);
            g_pEventLoopManager->addTimer(timer);
        } else
            timer->updateTimeout(std::chrono::milliseconds(100));
        if (!apply(proposed))
            rollback();
        return phase;
    }
    if (command == "confirm" || command == "finish" || command == "cancel" || command == "revert" || command == "status") {
        input >> id;
        if (id != currentID || id.empty())
            return "unknown_request";
        if ((command == "cancel" && pending()) || (command == "revert" && (pending() || phase == "committed")))
            rollback();
        if (command == "confirm" && phase == "preview")
            phase = "confirming";
        if (command == "finish" && phase == "confirming") {
            committed = proposed;
            phase     = "committed";
        }
        return phase;
    }
    if (command != "snapshot")
        return "invalid_command";
    std::ostringstream json;
    json << "{\"topology\":\"" << revision() << "\",\"supported\":" << (supported() ? "true" : "false") << ",\"pending\":" << (pending() ? "true" : "false") << ",\"outputs\":[";
    bool first = true;
    for (const auto& m : State::monitorState()->monitors()) {
        if (!first)
            json << ',';
        first = false;
        json << "{\"key\":\"" << keyFor(m) << "\",\"name\":\"" << escapeJSONStrings(m->m_name) << "\",\"x\":" << m->m_position.x << ",\"y\":" << m->m_position.y
             << ",\"width\":" << m->m_size.x << ",\"height\":" << m->m_size.y << ",\"scale\":" << m->m_scale << '}';
    }
    json << "]}";
    return json.str();
}
