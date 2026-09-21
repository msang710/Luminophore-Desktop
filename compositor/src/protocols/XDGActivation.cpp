#include "XDGActivation.hpp"
#include "../managers/TokenManager.hpp"
#include "../Compositor.hpp"
#include "core/Compositor.hpp"
#include <algorithm>
#include "core/Seat.hpp"
#include "../managers/SeatManager.hpp"

CXDGActivationToken::CXDGActivationToken(SP<CXdgActivationTokenV1> resource_) : m_resource(resource_) {
    if UNLIKELY (!resource_->resource())
        return;

    m_resource->setDestroy([this](CXdgActivationTokenV1* r) { PROTO::activation->destroyToken(this); });
    m_resource->setOnDestroy([this](CXdgActivationTokenV1* r) { PROTO::activation->destroyToken(this); });

    m_resource->setSetSerial([this](CXdgActivationTokenV1* r, uint32_t serial_, wl_resource* seat) {
        if (m_committed)
            return;
        m_serial = serial_;
        m_seat   = CWLSeatResource::fromResource(seat);
    });
    m_resource->setSetSurface([this](CXdgActivationTokenV1* r, wl_resource* surface) {
        if (m_committed)
            return;
        auto source = CWLSurfaceResource::fromResource(surface);
        if (source && source->client() == r->client())
            m_source = source;
        else
            m_source.reset();
    });

    m_resource->setSetAppId([this](CXdgActivationTokenV1* r, const char* appid) {
        if (!m_committed)
            m_appID = appid;
    });

    m_resource->setCommit([this](CXdgActivationTokenV1* r) {
        // TODO: should we send a protocol error of already_used here
        // if it was used? the protocol spec doesn't say _when_ it should be sent...
        if UNLIKELY (m_committed) {
            LOGM(Log::WARN, "possible protocol error, two commits from one token. Ignoring.");
            return;
        }

        m_committed = true;
        // send done with a new token
        m_token = g_pTokenManager->registerNewToken({}, std::chrono::months{12});

        LOGM(Log::DEBUG, "assigned new xdg-activation token");

        m_resource->sendDone(m_token.c_str());

        SP<Desktop::View::CWindow> sourceWindow;
        const auto                 seat   = m_seat.lock();
        const auto                 source = m_source.lock();
        if (source && source->m_mapped && seat && seat->client() == m_resource->client() && g_pSeatManager && g_pSeatManager->serialValid(seat, m_serial, false))
            sourceWindow = Desktop::viewState()->query().type(Desktop::View::VIEW_TYPE_WINDOW).surface(source).runWindow();
        PROTO::activation->m_sentTokens.push_back({m_token, m_resource->client(), sourceWindow, m_appID, std::chrono::steady_clock::now()});

        auto count = std::ranges::count_if(PROTO::activation->m_sentTokens, [this](const auto& other) { return other.client == m_resource->client(); });

        if UNLIKELY (count > 10) {
            // remove first token. Too many, dear app.
            for (auto i = PROTO::activation->m_sentTokens.begin(); i != PROTO::activation->m_sentTokens.end(); ++i) {
                if (i->client == m_resource->client()) {
                    g_pTokenManager->removeToken(g_pTokenManager->getToken(i->token));
                    PROTO::activation->m_sentTokens.erase(i);
                    break;
                }
            }
        }
    });
}

CXDGActivationToken::~CXDGActivationToken() {
    // xdg_activation_token_v1.destroy does not invalidate the issued token.
    ;
}

bool CXDGActivationToken::good() {
    return m_resource->resource();
}

CXDGActivationProtocol::CXDGActivationProtocol(const wl_interface* iface, const int& ver, const std::string& name) : IWaylandProtocol(iface, ver, name) {
    ;
}

void CXDGActivationProtocol::bindManager(wl_client* client, void* data, uint32_t ver, uint32_t id) {
    const auto RESOURCE = m_managers.emplace_back(makeUnique<CXdgActivationV1>(client, ver, id)).get();
    RESOURCE->setOnDestroy([this](CXdgActivationV1* p) { this->onManagerResourceDestroy(p->resource()); });

    RESOURCE->setDestroy([this](CXdgActivationV1* pMgr) { this->onManagerResourceDestroy(pMgr->resource()); });
    RESOURCE->setGetActivationToken([this](CXdgActivationV1* pMgr, uint32_t id) { this->onGetToken(pMgr, id); });
    RESOURCE->setActivate([this](CXdgActivationV1* pMgr, const char* token, wl_resource* surface) {
        auto TOKEN = std::ranges::find_if(m_sentTokens, [token](const auto& t) { return t.token == token; });

        if UNLIKELY (TOKEN == m_sentTokens.end()) {
            LOGM(Log::WARN, "activate event for non-existent token");
            return;
        }

        const auto context = *TOKEN;
        // Both the activation and its placement metadata are one-shot.
        m_sentTokens.erase(TOKEN);
        g_pTokenManager->removeToken(g_pTokenManager->getToken(context.token));

        SP<CWLSurfaceResource> surf    = CWLSurfaceResource::fromResource(surface);
        const auto             PWINDOW = Desktop::viewState()->query().type(Desktop::View::VIEW_TYPE_WINDOW).surface(surf).runWindow();

        std::erase_if(m_placedSurfaces, [](const auto& s) { return !s; });
        std::erase_if(m_pendingOrigins, [](const auto& p) { return !p.target || std::chrono::steady_clock::now() - p.issued > std::chrono::seconds(30); });
        const bool placed = std::ranges::any_of(m_placedSurfaces, [&](const auto& s) { return s.lock() == surf; });
        if (surf && !surf->m_mapped && !placed && context.source && std::chrono::steady_clock::now() - context.issued <= std::chrono::seconds(30)) {
            // Multiple origins for one target are ambiguous: keep an invalid entry.
            auto pending = std::ranges::find_if(m_pendingOrigins, [&](const auto& p) { return p.target.lock() == surf; });
            if (pending != m_pendingOrigins.end())
                pending->source.reset();
            else if (m_pendingOrigins.size() < 128)
                m_pendingOrigins.push_back({surf, context.source, context.appID, context.issued});
        }
        if UNLIKELY (!PWINDOW) {
            LOGM(Log::DEBUG, "activation has no window yet; initial placement may consume its origin");
            return;
        }

        PWINDOW->activate();
    });
}

void CXDGActivationProtocol::onManagerResourceDestroy(wl_resource* res) {
    std::erase_if(m_managers, [&](const auto& other) { return other->resource() == res; });
}

void CXDGActivationProtocol::destroyToken(CXDGActivationToken* token) {
    std::erase_if(m_tokens, [&](const auto& other) { return other.get() == token; });
}

void CXDGActivationProtocol::onGetToken(CXdgActivationV1* pMgr, uint32_t id) {
    const auto CLIENT   = pMgr->client();
    const auto RESOURCE = m_tokens.emplace_back(makeUnique<CXDGActivationToken>(makeShared<CXdgActivationTokenV1>(CLIENT, pMgr->version(), id))).get();

    if UNLIKELY (!RESOURCE->good()) {
        pMgr->noMemory();
        m_tokens.pop_back();
        return;
    }
}

SP<Desktop::View::CWindow> CXDGActivationProtocol::takePlacementSource(const SP<CWLSurfaceResource>& target, const std::string& appID) {
    if (!target)
        return {};
    std::erase_if(m_placedSurfaces, [](const auto& s) { return !s; });
    if (std::ranges::any_of(m_placedSurfaces, [&](const auto& s) { return s.lock() == target; }))
        return {};
    m_placedSurfaces.push_back(target);
    const auto pending = std::ranges::find_if(m_pendingOrigins, [&](const auto& p) { return p.target.lock() == target; });
    if (pending == m_pendingOrigins.end())
        return {};
    const auto context = *pending;
    m_pendingOrigins.erase(pending);
    const auto source = context.source.lock();
    if (!source || !source->m_isMapped || std::chrono::steady_clock::now() - context.issued > std::chrono::seconds(30) || (!context.appID.empty() && context.appID != appID))
        return {};
    return source;
}
