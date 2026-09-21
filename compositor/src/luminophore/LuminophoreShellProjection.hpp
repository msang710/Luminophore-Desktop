#pragma once

#include "LuminophoreVisualSettings.hpp"

#include <cstdint>
#include <chrono>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "../helpers/memory/Memory.hpp"
#include "../helpers/Color.hpp"
#include "../desktop/DesktopTypes.hpp"
#include "../defines.hpp"

namespace Render {
    class CLuminophoreShellBloom;
    struct SLuminophoreShellBloomDiagnostics;
}

class CEventLoopTimer;

namespace Luminophore {

    enum eShellProjectionPhase : uint8_t {
        SHELL_PROJECTION_PREPARE = 0,
        SHELL_PROJECTION_COMMIT,
        SHELL_PROJECTION_ABORT,
    };

    enum eShellProjectionPlane : uint8_t {
        SHELL_PROJECTION_BOTTOM = 0,
        SHELL_PROJECTION_OVERLAY,
    };

    enum eShellWidgetRole : uint8_t {
        SHELL_WIDGET_PASSIVE = 0,
        SHELL_WIDGET_OSD     = 3,
    };

    enum eShellSnapshotState : uint8_t {
        SHELL_SNAPSHOT_PREPARED = 0,
        SHELL_SNAPSHOT_COMMITTED,
        SHELL_SNAPSHOT_PRESENTED,
    };

    struct SShellProjectionStyle {
        CHyprColor color          = CHyprColor(0.612F, 0.796F, 0.984F, 1.F);
        float      radius         = 14.F;
        float      outline        = 2.F;
        float      extent         = 64.F;
        float      intensity      = 1.F;
        float      phase          = 0.F;
        float      revealFrom     = 1.F;
        float      revealTo       = 1.F;
        double     revealStarted  = 0.0;
        float      revealDuration = 0.F;
        Vector2D   revealOffset;
        CBox       panel;
        bool       bloom = false;
        CBox       blurPanel;
    };

    struct SShellProjectionFrame {
        Vector2D offset;
        float    opacity = 1.F;
        bool     active  = false;
        CBox     panel;
    };

    struct SShellSurfaceFrameSnapshot {
        std::string           surfaceNamespace;
        std::string           generation;
        uint64_t              revision        = 0;
        uint64_t              contentRevision = 0;
        PHLLSREF              surface;
        PHLMONITORREF         monitor;
        eShellWidgetRole      role  = SHELL_WIDGET_PASSIVE;
        eShellProjectionPlane plane = SHELL_PROJECTION_BOTTOM;
        CBox                  contentBox;
        CBox                  renderBox;
        CBox                  blurBox;
        CBox                  bloomBox;
        CBox                  hitBox;
        Vector2D              offset;
        float                 opacity = 1.F;
        SShellProjectionStyle style;
        SVisualBundle         visualBundle;
        uint64_t              visualRevision = 0;
        eShellSnapshotState   state          = SHELL_SNAPSHOT_PREPARED;
        bool                  mapped         = false;
        bool                  visible        = false;
    };

    using PShellSurfaceFrameSnapshot = std::shared_ptr<const SShellSurfaceFrameSnapshot>;

    struct SShellProjectionReceipt {
        std::string         generation;
        uint64_t            revision        = 0;
        uint64_t            contentRevision = 0;
        eShellSnapshotState state           = SHELL_SNAPSHOT_PREPARED;
    };

    class CLuminophoreShellProjection {
      public:
        CLuminophoreShellProjection();
        ~CLuminophoreShellProjection();

        bool transact(const std::string& surfaceNamespace, const std::string& generation, uint64_t revision, uint64_t contentRevision, eShellProjectionPhase phase,
                      eShellProjectionPlane requestedPlane, eShellWidgetRole role, const SShellProjectionStyle& style);
        std::vector<SShellProjectionReceipt>    receiptsFor(const std::string& surfaceNamespace) const;
        std::optional<CHyprColor>               accentForMonitor(PHLMONITOR monitor) const;
        void                                    visualSettingsChanged();
        void                                    enqueueBloom(const PShellSurfaceFrameSnapshot& snapshot);
        SShellProjectionFrame                   frameFor(PHLLS surface, PHLMONITOR monitor) const;
        PShellSurfaceFrameSnapshot              snapshotFor(PHLLS surface, PHLMONITOR monitor) const;
        std::vector<PShellSurfaceFrameSnapshot> renderList(PHLMONITOR monitor, eShellProjectionPlane plane) const;
        bool                                    isProjected(PHLLS surface, PHLMONITOR monitor) const;
        bool                                    isProjectedBottom(PHLLS surface, PHLMONITOR monitor) const;
        bool                                    isProjectedOverlay(PHLLS surface, PHLMONITOR monitor) const;
        std::vector<PHLLSREF>                   projectedOverlaySurfaces(PHLMONITOR monitor) const;
        std::vector<PHLLSREF>                   physicalBottomSurfaces(PHLMONITOR monitor) const;
        void                                    applyPendingForMonitor(PHLMONITOR monitor);
        void                                    resolveFrameForMonitor(PHLMONITOR monitor);
        void                                    presentedForMonitor(PHLMONITOR monitor);
        bool                                    blocksDirectScanout(PHLMONITOR monitor) const;
        Render::SLuminophoreShellBloomDiagnostics      bloomDiagnostics() const;

      private:
        struct SSnapshot {
            std::string                           surfaceNamespace;
            std::string                           generation;
            uint64_t                              revision        = 0;
            uint64_t                              contentRevision = 0;
            eShellProjectionPlane                 requestedPlane  = SHELL_PROJECTION_BOTTOM;
            eShellWidgetRole                      role            = SHELL_WIDGET_PASSIVE;
            SShellProjectionStyle                 style;
            PHLLSREF                              surface;
            PHLMONITORREF                         monitor;
            std::chrono::steady_clock::time_point queuedAt;
        };

        using PSnapshot = std::shared_ptr<const SSnapshot>;

        PSnapshot                  makeSnapshot(const std::string& surfaceNamespace, const std::string& generation, uint64_t revision, uint64_t contentRevision,
                                                eShellProjectionPlane requestedPlane, eShellWidgetRole role, const SShellProjectionStyle& style) const;
        bool                       stillBound(const PSnapshot& snapshot) const;
        eShellProjectionPlane      effectivePlaneFor(const PSnapshot& snapshot) const;
        CBox                       panelFor(const PSnapshot& snapshot) const;
        PShellSurfaceFrameSnapshot resolveSnapshot(const PSnapshot& snapshot, PHLMONITOR monitor, eShellSnapshotState state) const;
        void                       damagePanel(const PSnapshot& snapshot, const CBox& panel) const;
        void                       damageFrameSnapshot(const PShellSurfaceFrameSnapshot& snapshot) const;
        void                       damageSnapshot(const PSnapshot& snapshot) const;
        bool                       hasAnimatedBloom(PHLMONITOR monitor) const;
        void                       scheduleBloomFrame(PHLMONITOR monitor);
        void                       requestBloomFrame(MONITORID monitorID);
        void                       discardExpired();
        struct SMonitorBloomAnimation {
            PHLMONITORREF       monitor;
            SP<CEventLoopTimer> timer;
        };
        std::unordered_map<std::string, PSnapshot>                  m_prepared;
        std::unordered_map<std::string, PSnapshot>                  m_pending;
        std::unordered_map<std::string, PSnapshot>                  m_committed;
        std::unordered_map<std::string, PSnapshot>                  m_awaitingPresentation;
        std::unordered_map<std::string, uint64_t>                   m_latestRevision;
        std::unordered_map<MONITORID, SMonitorBloomAnimation>       m_bloomAnimations;
        std::unordered_map<MONITORID, eShellProjectionPlane>        m_framePlanes;
        std::unordered_map<std::string, CBox>                       m_framePanels;
        std::unordered_map<std::string, PShellSurfaceFrameSnapshot> m_frameSnapshots;
        UP<Render::CLuminophoreShellBloom>                                 m_bloom;
    };

    UP<CLuminophoreShellProjection>& shellProjection();

}
