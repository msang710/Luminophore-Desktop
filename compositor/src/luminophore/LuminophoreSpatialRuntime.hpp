#pragma once
#include "LuminophoreSpatialResizeGesture.hpp"
#include "LuminophoreSurfaceSource.hpp"
#include "LuminophoreSpatialHistory.hpp"

#include "LuminophoreSpatialWindowRegistry.hpp"
#include "LuminophoreSpatialEvents.hpp"

#include "LuminophoreSpatialCommandQueue.hpp"
#include "LuminophoreSpatialCommitter.hpp"
#include "../desktop/DesktopTypes.hpp"
#include "../helpers/memory/Memory.hpp"
#include "../helpers/math/Math.hpp"

#include <optional>
#include <map>
#include <vector>

namespace Layout {
    class ITarget;
}

namespace Luminophore {
    struct SSpatialWindowSnapshot {
        LuminophoreWindowKey                       key             = 0;
        SLuminophoreBoardPoint                     coordinate      = {};
        SLuminophorePhysicalBox                    committedBox    = {};
        uint64_t                                   primaryOutputID = 0;
        std::vector<SLuminophoreProjectedFragment> fragments;
        bool                                       visible  = false;
        bool                                       floating = false;
        uint64_t                                   boardID  = 0;
        SSurfaceSourceSnapshot                     source;
        bool                                       presentationAvailable = false;
    };

    struct SSpatialSnapshot {
        bool                                        active                    = false;
        bool                                        committed                 = false;
        uint64_t                                    revision                  = 0;
        uint64_t                                    topologyRevision          = 0;
        uint64_t                                    committedModelRevision    = 0;
        uint64_t                                    committedTopologyRevision = 0;
        SLuminophoreBoardExtent                     extent                    = {};
        SLuminophoreViewRect                        view                      = {};
        eLuminophorePresentationMode                presentationMode          = eLuminophorePresentationMode::NORMAL;
        std::vector<SLuminophoreOutputView>         outputViews               = {};
        std::optional<LuminophoreWindowKey>         wideKey                   = std::nullopt;
        std::optional<LuminophoreWindowKey>         focusedKey                = std::nullopt;
        uint64_t                                    selectedOutputID          = 0;
        std::vector<SLuminophorePhysicalOutput>     outputs;
        std::vector<SSpatialWindowSnapshot>         windows;
        std::optional<SLuminophoreIndependentState> independent;
    };

    class CLuminophoreSpatialRuntime {
      public:
        void                                          bootstrap();
        void                                          observeTarget(const SP<Layout::ITarget>& target, std::optional<CBox> desiredFloatingBox = std::nullopt);
        void                                          topologyChanged();
        void                                          focusChanged();
        void                                          advanceMotion();
        void                                          resizeGesture(bool active, bool cancelled = false);
        eLuminophoreHistoryResult                     historyReplay(bool redo);
        eLuminophoreHistoryResult                     historyRequest(bool redo, std::optional<uint64_t> expected, const std::string& id, const std::string& source);
        void                                          historyActionBegin();
        void                                          historyActionEnd();
        bool                                          captureGeometryHistory(const SP<Layout::ITarget>& target) const;
        void                                          beginAuxiliaryGesture(const SP<Layout::ITarget>& target);
        void                                          endAuxiliaryGesture(bool cancelled);
        size_t                                        undoCount() const;
        size_t                                        redoCount() const;
        bool                                          resizeTiled(const SP<Layout::ITarget>& target, const Vector2D& delta, bool left, bool top);
        std::vector<CBox>                             regionsFor(const PHLWINDOW& window, const PHLMONITOR& monitor) const;
        bool                                          commitCurrent();
        void                                          beginFloatingMotion(const SP<Layout::ITarget>& target);
        bool                                          updateFloatingMotion(const SP<Layout::ITarget>& target, const CBox& box);
        void                                          finishFloatingMotion(const SP<Layout::ITarget>& target);
        bool                                          setFloatingGeometry(const SP<Layout::ITarget>& target, const CBox& box);
        bool                                          managesFloatingTarget(const SP<Layout::ITarget>& target) const;
        bool                                          managesTiledTarget(const SP<Layout::ITarget>& target) const;
        bool                                          dispatch(eSpatialAction action, eLuminophoreSpatialDirection direction, std::optional<PHLWINDOW> window = std::nullopt);
        SLuminophoreSpatialTransactionResult          edit(const SLuminophoreSpatialCommand& command, bool previewOnly);
        bool                                          revealWindow(PHLWINDOW window);
        bool                                          active() const;
        bool                                          desktopExposed() const;
        uint64_t                                      revision() const;
        uint64_t                                      observationGeneration() const;
        std::optional<SLuminophoreNormalizedBox>      floatingGeometry(LuminophoreWindowKey key) const;
        SSpatialSnapshot                              snapshot() const;
        static eSpatialParticipation                  classifyParticipation(const SSpatialParticipationFacts& facts);
        std::optional<SLuminophoreSpatialCommitEntry> presentationFor(LuminophoreWindowKey key) const;
        std::optional<SLuminophoreSpatialCommitEntry> presentationFor(const PHLWINDOW& window) const;
        bool                                          requiresComposition(const PHLMONITOR& monitor) const;
        bool                                          isWideWindow(const PHLWINDOW& window) const;
        bool                                          isBoardRoot(const PHLWINDOW& window) const;

        std::vector<SLuminophoreProjectedCell>        projectedCells() const;

        std::optional<SLuminophorePhysicalBox>        projectedHostBox(const SLuminophoreBoardPoint& point, uint64_t outputID = 0) const;

      private:
        bool                                                              m_historyRecoveryFailed    = false;
        bool                                                              m_historyPreflightRejected = false;
        void                                                              applyHistoryGeometry(const SLuminophoreHistoryFrame& frame);
        void                                                              applyHistoryOwnership(const SLuminophoreSpatialSnapshot& snapshot);
        size_t                                                            m_historyActionDepth = 0;
        std::optional<SLuminophoreHistoryFrame>                           m_historyActionStart;
        bool                                                              applyHistoryNative(const SLuminophoreHistoryFrame& frame);
        bool                                                              commitHistory(const SLuminophoreSpatialSnapshot& snapshot, const SLuminophoreHistoryFrame& frame);
        CLuminophoreSpatialHistory                                        m_history;
        CLuminophoreHistoryRequests                                       m_historyRequests;
        CLuminophoreHistoryGeometryGesture                                m_auxiliaryGesture;
        std::map<LuminophoreWindowKey, std::pair<PHLWINDOWREF, uint64_t>> m_lifetimes;
        std::map<LuminophoreWindowKey, bool>                              m_directLaunches;
        std::map<LuminophoreWindowKey, std::string>                       m_launchGroups;
        uint64_t                                                          m_nextLifetime     = 0;
        bool                                                              m_restoringHistory = false;
        std::optional<SLuminophoreHistoryFrame>                           m_resizeStart;
        SLuminophoreHistoryFrame                                          historyFrame();
        void                                                              recordHistory(SLuminophoreHistoryFrame before, const std::string& group = {});
        Spatial::CResizeGesture                                           m_resizeGesture;
        std::map<LuminophoreWindowKey, SLuminophoreSpatialCommitEntry>    m_exits;
        CLuminophoreSpatialWindowRegistry                                 m_windows;
        std::optional<SLuminophoreSpatialCommitEntry>                     floatingMotionPresentation(LuminophoreWindowKey key) const;
        SLuminophoreSpatialTransactionResult                              submit(LuminophoreSpatialPayload payload);
        bool                                                              commitSnapshot(const SLuminophoreSpatialSnapshot& snapshot);
        void                                                              applyFocusUpdate(const SLuminophoreSpatialTransactionResult& result);
        void                                                              notifyStateChanged();
        SP<Layout::ITarget>                                               targetFor(LuminophoreWindowKey key) const;
        std::optional<LuminophoreWindowKey>                               focusedKey(std::optional<PHLWINDOW> window);
        std::optional<uint64_t>                                           selectedOutputID(std::optional<PHLWINDOW> window = std::nullopt) const;
        std::optional<LuminophoreWindowKey>                               anchorKeyForOutput(uint64_t outputID, std::optional<PHLWINDOW> window);
        std::optional<SLuminophoreBoardPoint>                             anchorForOutput(uint64_t outputID, std::optional<LuminophoreWindowKey> key) const;
        std::optional<SLuminophoreBoardPoint>                             preferredPoint(const PHLWINDOW& window, bool allowOccupied) const;
        eSpatialParticipation                                             participationFor(const SP<Layout::ITarget>& target) const;

        UP<CLuminophoreSpatialModel>                                      m_model;
        CLuminophoreSpatialCommandQueue                                   m_commandQueue;
        CLuminophoreSpatialCommitter                                      m_committer;
        PHLWINDOWREF                                                      m_desktopFocus;
        std::vector<SLuminophorePhysicalOutput>                           m_outputs;
        bool                                                              m_active           = false;
        bool                                                              m_bootstrapped     = false;
        uint64_t                                                          m_notifiedRevision = 0;
        uint64_t                                                          m_topologyRevision = 0;
        std::optional<LuminophoreWindowKey>                               m_anchorKey;
        std::optional<LuminophoreWindowKey>                               m_committedFocusKey;
    };

    UP<CLuminophoreSpatialRuntime>& spatialRuntime();
}
