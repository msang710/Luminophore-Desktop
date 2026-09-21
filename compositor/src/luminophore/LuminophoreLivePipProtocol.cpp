#include "LuminophoreLivePipProtocol.hpp"
#include "LuminophoreLivePipController.hpp"
#include "../Compositor.hpp"
#include "../helpers/MiscFunctions.hpp"
#include "../managers/SessionLockManager.hpp"
#include <sstream>
#include <charconv>
#include <format>
#include <cmath>

using namespace Luminophore;
static bool pipToken(const std::string& s) {
    return !s.empty() && s.size() <= 256 && s.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:") == std::string::npos;
}
static std::optional<uint64_t> pipNumber(const std::string& s) {
    uint64_t   n = 0;
    const auto r = std::from_chars(s.data(), s.data() + s.size(), n);
    return r.ec == std::errc{} && r.ptr == s.data() + s.size() ? std::optional{n} : std::nullopt;
}
std::optional<SPipCommand> CLuminophoreLivePipProtocol::parse(const std::string& raw) {
    if (raw.size() > 2048)
        return std::nullopt;
    std::istringstream in(raw);
    SPipCommand        c;
    if (!(in >> c.action >> c.request >> c.instance) || !pipToken(c.request) || !pipToken(c.instance))
        return std::nullopt;
    const auto rect = [&] {
        return bool(in >> c.rect.x >> c.rect.y >> c.rect.width >> c.rect.height) && CLuminophoreLivePipModel::validRect({c.rect.x, c.rect.y, c.rect.width, c.rect.height}) &&
            std::abs(c.rect.x) < 1e7 && std::abs(c.rect.y) < 1e7 && c.rect.width < 1e6 && c.rect.height < 1e6;
    };
    if (c.action == "resolve") {
        if (!rect())
            return std::nullopt;
    } else if (c.action == "create") {
        if (!(in >> c.output) || !pipToken(c.output) || !rect() || !(in >> c.margin) || !std::isfinite(c.margin) || c.margin < 0 || c.margin > 256)
            return std::nullopt;
    } else if (c.action == "remove" || c.action == "place") {
        std::string revision;
        if (!(in >> revision) || !pipNumber(revision) || !pipNumber(c.request))
            return std::nullopt;
        c.revision = *pipNumber(revision);
        if (c.action == "place" && (!(in >> c.output) || !pipToken(c.output) || !rect()))
            return std::nullopt;
    } else if (c.action != "begin" && c.action != "cancel" && c.action != "result")
        return std::nullopt;
    std::string extra;
    if (in >> extra)
        return std::nullopt;
    return c;
}
UP<CLuminophoreLivePipProtocol>& Luminophore::livePipProtocol() {
    static auto p = makeUnique<CLuminophoreLivePipProtocol>();
    return p;
}

std::string CLuminophoreLivePipProtocol::execute(const SPipCommand& c) {
    const auto reply = [&](const char* status) {
        return std::format(R"({{"status":"{}","request":"{}","instance":"{}"}})", status, escapeJSONStrings(c.request), escapeJSONStrings(g_pCompositor->m_instanceSignature));
    };
    if (c.instance != g_pCompositor->m_instanceSignature)
        return reply("stale");
    if (g_pSessionLockManager->isSessionLocked()) {
        m_sessions.clear();
        return reply("locked");
    }
    std::erase_if(m_sessions, [](const auto& item) { return Time::steadyNow() - item.second.started > std::chrono::seconds(180); });
    auto* controller = livePipController().get();
    if (c.action == "remove")
        return reply(controller->remove(*pipNumber(c.request), c.revision) ? "applied" : "stale");
    if (c.action == "place")
        return reply(controller->place(*pipNumber(c.request), c.revision, c.output, {c.rect.x, c.rect.y, c.rect.width, c.rect.height}) ? "applied" : "stale");
    if (c.action == "begin") {
        if (m_sessions.contains(c.request))
            return m_sessions.at(c.request).result.empty() ? reply("ready") : m_sessions.at(c.request).result;
        if (m_sessions.size() >= 32)
            return reply("busy");
        m_sessions.emplace(c.request, SSession{.scene = CLuminophoreLivePipSelection::snapshot(), .started = Time::steadyNow()});
        return reply("ready");
    }
    const auto it = m_sessions.find(c.request);
    if (it == m_sessions.end())
        return reply("unknown");
    auto& session = it->second;
    if (!session.result.empty())
        return session.result;
    if (c.action == "cancel") {
        session.result = reply("cancelled");
        return session.result;
    }
    if (c.action == "result")
        return reply("pending");
    const auto current = CLuminophoreLivePipSelection::snapshot();
    if (current.signature != session.scene.signature) {
        session.result = reply("stale");
        return session.result;
    }
    if (c.action == "resolve") {
        if (session.selectedBox && *session.selectedBox != c.rect)
            return reply("invalid-region");
        session.selection = CLuminophoreLivePipSelection::resolve(session.scene, c.rect);
        if (!session.selection)
            return reply("invalid-region");
        session.selectedBox = c.rect;
        const auto& crop    = session.selection->crop;
        return std::format(R"({{"status":"ready","request":"{}","instance":"{}","crop":[{},{},{},{}]}})", c.request, escapeJSONStrings(c.instance), crop.x, crop.y, crop.width,
                           crop.height);
    }
    if (c.action != "create" || !session.selection)
        return reply("invalid-region");
    const auto& selection = *session.selection;
    const auto  source    = selection.source.source->snapshot();
    if (source.token != selection.source.state.token || source.extentRevision != selection.source.state.extentRevision || !source.alive) {
        session.result = reply("source-unavailable");
        return session.result;
    }
    if (controller->count() >= 4) {
        session.result = reply("limit-reached");
        return session.result;
    }
    controller->setEdgeMargin(c.margin);
    const auto id =
        controller->create(selection.source.source, controller->revision(), source.extentRevision, selection.crop, c.output, {c.rect.x, c.rect.y, c.rect.width, c.rect.height});
    session.result =
        id ? std::format(R"({{"status":"applied","request":"{}","instance":"{}","id":"{}"}})", c.request, escapeJSONStrings(c.instance), *id) : reply("source-unavailable");
    return session.result;
}
