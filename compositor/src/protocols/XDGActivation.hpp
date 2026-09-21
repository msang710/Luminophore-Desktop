#pragma once

#include <vector>
#include <cstdint>
#include <chrono>

class CWLSurfaceResource;
class CWLSeatResource;
namespace Desktop::View {
    class CWindow;
}
#include "WaylandProtocol.hpp"
#include "xdg-activation-v1.hpp"

class CXDGActivationToken {
  public:
    CXDGActivationToken(SP<CXdgActivationTokenV1> resource_);
    ~CXDGActivationToken();

    bool good();

  private:
    SP<CXdgActivationTokenV1> m_resource;

    uint32_t                  m_serial    = 0;
    std::string               m_appID     = "";
    bool                      m_committed = false;

    std::string               m_token = "";
    WP<CWLSurfaceResource>    m_source;
    WP<CWLSeatResource>       m_seat;

    friend class CXDGActivationProtocol;
};

class CXDGActivationProtocol : public IWaylandProtocol {
  public:
    CXDGActivationProtocol(const wl_interface* iface, const int& ver, const std::string& name);

    virtual void bindManager(wl_client* client, void* data, uint32_t ver, uint32_t id);

    // Consumes origin at the first placement attempt, including excluded roles.
    SP<Desktop::View::CWindow> takePlacementSource(const SP<CWLSurfaceResource>& target, const std::string& appID);

  private:
    void onManagerResourceDestroy(wl_resource* res);
    void destroyToken(CXDGActivationToken* pointer);
    void onGetToken(CXdgActivationV1* pMgr, uint32_t id);

    struct SSentToken {
        std::string                           token;
        wl_client*                            client = nullptr; // READ-ONLY: can be dead
        WP<Desktop::View::CWindow>            source;
        std::string                           appID;
        std::chrono::steady_clock::time_point issued;
    };
    struct SPendingOrigin {
        WP<CWLSurfaceResource>                target;
        WP<Desktop::View::CWindow>            source;
        std::string                           appID;
        std::chrono::steady_clock::time_point issued;
    };
    std::vector<SSentToken>             m_sentTokens;
    std::vector<SPendingOrigin>         m_pendingOrigins;
    std::vector<WP<CWLSurfaceResource>> m_placedSurfaces;

    //
    std::vector<UP<CXdgActivationV1>>    m_managers;
    std::vector<UP<CXDGActivationToken>> m_tokens;

    friend class CXDGActivationToken;
};

namespace PROTO {
    inline UP<CXDGActivationProtocol> activation;
};
