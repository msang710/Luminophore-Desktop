#pragma once

#include <functional>
#include <wayland-server-core.h>

namespace Luminophore {
    // A client survives replacement of any individual surface it owns.
    class CClientLifetime {
      public:
        CClientLifetime();
        ~CClientLifetime();
        CClientLifetime(const CClientLifetime&)            = delete;
        CClientLifetime& operator=(const CClientLifetime&) = delete;
        bool             bind(wl_client* client, std::function<void()> disconnected);
        bool             lost() const;
        bool             accepts(wl_client* client) const;
        void             reset();

      private:
        struct SListener {
            wl_listener      listener = {};
            CClientLifetime* owner    = nullptr;
        } m_destroy;
        static void           destroyed(wl_listener* listener, void* data);
        wl_client*            m_client = nullptr;
        bool                  m_bound  = false;
        std::function<void()> m_disconnected;
    };
}
