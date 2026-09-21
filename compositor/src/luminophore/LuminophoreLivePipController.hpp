#pragma once
#include "LuminophoreLivePipModel.hpp"
#include "../helpers/time/Time.hpp"
#include "../desktop/DesktopTypes.hpp"
#include <set>
#include "../helpers/math/Math.hpp"

namespace Luminophore {
    // Main-event-loop owner. No surfaces of its own and no board participation.
    class CLuminophoreLivePipController {
      public:
        std::optional<uint64_t> create(const SP<CLuminophoreSurfaceSource>&, uint64_t expectedRevision, uint64_t expectedExtentRevision, SLivePipRect crop, const std::string& output,
                                       SLivePipRect destination);
        bool              update(uint64_t id, uint64_t expectedRevision, uint64_t expectedExtentRevision, SLivePipRect crop, const std::string& output, SLivePipRect destination);
        bool              remove(uint64_t id, uint64_t expectedRevision);
        void              render(PHLMONITOR, const Time::steady_tp&);
        bool              occupies(PHLMONITOR) const;
        std::string       query() const;
        bool              pointerButton(uint32_t button, bool pressed, Vector2D position);
        bool              pointerMotion(Vector2D position);
        bool              cancelPointer();
        bool              escape(bool pressed);
        bool              pointerOwned() const;
        bool              pointerAt(Vector2D position) const;
        void              reconcileOutputs();
        uint64_t          revision() const;
        size_t            count() const;
        void              setEdgeMargin(double margin);
        bool              place(uint64_t id, uint64_t revision, const std::string& output, SLivePipRect destination);

        std::vector<CBox> selectionBlockers() const;

      private:
        struct SSourceSubscription {
            SP<CLuminophoreSurfaceSource> source;
            SSurfaceSourceSnapshot previous;
            CHyprSignalListener    commit;
            CHyprSignalListener    destroy;
        };
        struct SGrab {
            uint64_t      id     = 0;
            bool          resize = false;
            Vector2D      origin;
            SLivePipEntry before;
        };
        std::optional<SGrab>                        m_grab;
        std::optional<uint64_t>                     m_hovered;
        Vector2D                                    m_pointer;
        std::set<uint32_t>                          m_consumedButtons;
        bool                                        m_escapeHeld = false;
        double                                      m_edgeMargin = 24;
        std::optional<uint64_t>                     hit(Vector2D position) const;
        CLuminophoreLivePipModel                           m_model;
        std::map<uint64_t, UP<SSourceSubscription>> m_sources;
        void                                        sourceChanged(uint64_t token);
        void                                        damage(const SLivePipEntry&) const;
    };
    UP<CLuminophoreLivePipController>& livePipController();
}
