#pragma once

#include "LuminophoreSpatialPreview.hpp"
#include "LuminophoreClientLifetime.hpp"
#include "LuminophoreSpatialProjection.hpp"
#include "../render/luminophore/LuminophoreSpatialBadgeRenderer.hpp"
#include "../helpers/time/Time.hpp"
#include "../helpers/AnimatedVariable.hpp"
#include "../managers/eventLoop/EventLoopManager.hpp"
#include "../helpers/memory/Memory.hpp"

class CWLSurfaceResource;

namespace Layout {
    class ITarget;
}

namespace Luminophore {
    struct SSpatialSnapshot;
    struct SSpatialGrabLayoutRegistration {
        uint64_t                     generation       = 0;
        uint64_t                     revision         = 0;
        uint64_t                     topologyRevision = 0;
        std::string                  frameGeneration;
        uint64_t                     frameRevision = 0;
        std::vector<SLuminophoreEditorCell> cells;
        uint64_t                     targetEpoch = 0;
    };

    class CLuminophoreSpatialGrabController {
      public:
        ~CLuminophoreSpatialGrabController();
        bool                begin(const SP<Layout::ITarget>& target, double x, double y, SLuminophoreDragOptions options = {});
        bool                beginFromEditor(LuminophoreWindowKey window, uint64_t revision, uint64_t topology, uint64_t output, uint32_t pressTime);
        bool                cancelFromEditor(uint32_t pressTime);
        bool                bindLayout(uint64_t generation, uint64_t revision, uint64_t topologyRevision, const std::string& frameGeneration, uint64_t frameRevision,
                                       const std::vector<SLuminophoreEditorCell>& cells, uint64_t targetEpoch = 0);
        void                update(double x, double y);
        void                end(bool cancelled, double x, double y, bool deferred = false);
        void                finishEnd();
        bool                active() const;
        bool                badgeAsset(uint64_t generation, uint64_t epoch, const std::string& mask, const CHyprColor& color);
        void                renderBadge(PHLMONITOR monitor, const Time::steady_tp& time);
        std::optional<CBox> visualBox(const PHLWINDOW& window) const;
        float               visualAlpha(const PHLWINDOW& window) const;

      private:
        bool m_preparingEnd = false;
        bool m_cancelEnding = false;
        struct STerminal {
            SLuminophoreSpatialGrabState                        state;
            std::optional<SLuminophoreSpatialTransactionResult> result;
            bool                                         cancelled = false;
        };
        std::optional<STerminal>        m_terminal;
        SP<bool>                        m_lifetime = makeShared<bool>(true);
        void                            processUpdate(double x, double y);
        UP<SEventLoopDoLaterLock>       m_pendingUpdate;
        CLuminophoreSpatialPreviewCache        m_previewCache;
        CLuminophoreSpatialPreviewQueue        m_updateQueue;
        uint64_t                        m_layoutGeneration = 0;
        std::optional<SLuminophoreEditorFrame> editorFrame() const;
        void publish(const char* phase, const SLuminophoreSpatialGrabState& state, const std::optional<SLuminophoreSpatialTransactionResult>& result = std::nullopt) const;
        std::optional<SLuminophoreSpatialCommand> directCommand(const SSpatialSnapshot& source, double x, double y) const;
        bool                               m_direct = false;
        SLuminophoreDragOptions                   m_options;
        uint32_t                           m_editorPressTime = 0;
        std::optional<SLuminophoreSpatialCommand> floatingCommand(const SSpatialSnapshot& source, double x, double y) const;
        Vector2D                           m_directStart;
        std::vector<SLuminophoreProjectedCell>    m_directCells;
        CLuminophoreSpatialGrab                   m_grab;
        WP<Layout::ITarget>                m_target;
        PHLANIMVAR<float>                  m_progress;
        CBox                               m_sourceBox;
        Render::CLuminophoreSpatialBadgeRenderer  m_badgeRenderer;
        CClientLifetime                    m_shellClient;
        PHLANIMVAR<float>                  m_colorProgress;
        CHyprColor                         m_previousBadgeColor;
        CHyprColor                         m_badgeColor = CHyprColor{0.4F, 0.8F, 1.F, 1.F};
        PHLLSREF                           m_editorSurface;
        bool                               m_layoutReady = false;
        void                               prepareVisual();
        CBox                               badgeBox() const;
        void                               damageVisual() const;
        double                             m_pointerX = 0.0;
        double                             m_pointerY = 0.0;
    };
    UP<CLuminophoreSpatialGrabController>& spatialGrabController();
}
