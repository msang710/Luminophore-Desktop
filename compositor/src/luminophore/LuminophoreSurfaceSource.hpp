#pragma once

#include "../helpers/memory/Memory.hpp"
#include "../helpers/signal/Signal.hpp"
#include <cstdint>
#include <optional>

class CWLSurfaceResource;
struct SSurfaceState;

namespace Luminophore {
    // Surface-local logical units, never a window's animated or projected box.
    struct SSourceExtent {
        double width                                  = 0;
        double height                                 = 0;
        bool   operator==(const SSourceExtent&) const = default;
    };

    struct SSurfaceSourceSnapshot {
        uint64_t                     token          = 0;
        uint64_t                     revision       = 0;
        uint64_t                     extentRevision = 0;
        bool                         alive          = false;
        bool                         mapped         = false;
        bool                         hasBuffer      = false;
        std::optional<SSourceExtent> extent;
    };

    // Owned by wl_surface, but holds only a weak reference back to it. A consumer
    // may retain this handle after destroy; it can never bind to another surface.
    class CLuminophoreSurfaceSource {
      public:
        static SP<CLuminophoreSurfaceSource> create(const SP<CWLSurfaceResource>& surface);
        SSurfaceSourceSnapshot        snapshot() const;
        SP<CWLSurfaceResource>        surface() const;
        static SSurfaceSourceSnapshot describe(uint64_t token, uint64_t revision, bool alive, bool mapped, bool hasBuffer, const SSurfaceState& committed,
                                               uint64_t extentRevision = 1);
        CLuminophoreSurfaceSource(const CLuminophoreSurfaceSource&)            = delete;
        CLuminophoreSurfaceSource& operator=(const CLuminophoreSurfaceSource&) = delete;

      private:
        explicit CLuminophoreSurfaceSource(const SP<CWLSurfaceResource>& surface);
        WP<CWLSurfaceResource>       m_surface;
        uint64_t                     m_token          = 0;
        uint64_t                     m_revision       = 1;
        uint64_t                     m_extentRevision = 1;
        std::optional<SSourceExtent> m_lastExtent;
        bool                         m_destroyed = false;
        CHyprSignalListener          m_commit;
        CHyprSignalListener          m_destroy;
    };
}
