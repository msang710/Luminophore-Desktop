#pragma once

#include "LuminophoreShellBloomPassElement.hpp"
#include "LuminophoreBloomPyramid.hpp"
#include "../../defines.hpp"
#include <chrono>
#include <string>
#include <unordered_map>

class CShader;

namespace Luminophore {
    struct SVisualBundle;
}

namespace Render {

    struct SLuminophoreShellBloomDiagnostics {
        uint64_t caches        = 0;
        uint64_t rendered      = 0;
        uint64_t regenerations = 0;
        uint64_t allocations   = 0;
        uint64_t fallbacks     = 0;
    };

    class CLuminophoreShellBloom {
      public:
        ~CLuminophoreShellBloom();

        void enqueue(const std::string& key, PHLMONITOR monitor, const CHyprColor& color, float radius, float outline, const Luminophore::SVisualBundle& visual, float phase,
                     const CBox& globalPanel, float opacity);
        void render(const CLuminophoreShellBloomPassElement::SData& data);
        void discard(const std::string& key);
        SLuminophoreShellBloomDiagnostics diagnostics() const;

      private:
        struct SCache {
            luminophore_bloom_pyramid                    bloom{};
            bool                                  initialized = false;
            std::chrono::steady_clock::time_point createdAt;
        };

        bool                                    ensureCompositeShader();
        bool                                    ensureQuad();
        SCache*                                 cacheFor(const std::string& key);

        SP<CShader>                             m_compositeShader;
        bool                                    m_shaderAttempted = false;
        GLuint                                  m_quad            = 0;
        std::unordered_map<std::string, SCache> m_caches;
        uint64_t                                m_rendered = 0;
    };

}
