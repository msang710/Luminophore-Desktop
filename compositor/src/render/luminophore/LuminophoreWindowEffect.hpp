#pragma once

#include "LuminophoreWindowEffectPassElement.hpp"
#include "../../defines.hpp"
#include "../../debug/LuminophoreDiagnostics.hpp"

class CShader;

namespace Render {

    class CLuminophoreFrameScheduler;

    class CLuminophoreWindowEffect {
      public:
        CLuminophoreWindowEffect() = default;
        ~CLuminophoreWindowEffect();

        void                         enqueue(PHLWINDOW window, PHLMONITOR monitor, const Vector2D& position, const Vector2D& size, float alpha);
        void                         enqueueCapture(PHLWINDOW window, PHLMONITOR monitor, const Vector2D& size);
        void                         render(const CLuminophoreWindowEffectPassElement::SData& data);
        void                         presented(PHLMONITOR monitor);

        SLuminophoreDiagnosticsSnapshot     diagnostics() const;
        std::vector<SLuminophoreFrameEvent> frameEvents() const;

      private:
        struct SDynamics {
            float intensity = 1.F;
            float radius    = 1.F;
        };

        SDynamics                           dynamics() const;
        CLuminophoreWindowEffectPassElement::SData makeData(PHLWINDOW window, PHLMONITOR monitor, const CBox& box, float alpha);
        bool                                ensureShader();
        void                                ensureScheduler();
        void                    renderRoundedPass(const CBox& box, const CHyprColor& color, float rounding, float roundingPower, int range, int glowPower, float alpha, bool mask);

        SP<CShader>             m_shader;
        UP<CLuminophoreFrameScheduler> m_scheduler;
        bool                    m_shaderAttempted = false;
        CLuminophoreDiagnostics        m_diagnostics;
    };

}
