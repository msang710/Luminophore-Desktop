#pragma once

#include "../helpers/memory/Memory.hpp"
#include "../render/BlurParameters.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace Luminophore {
    struct SVisualSettings {
        uint32_t    schemaVersion                            = 1;
        std::string preset                                   = "balanced";
        bool        enabled                                  = true;
        bool        breathing                                = true;
        double      intensity                                = 0.42;
        bool        operator==(const SVisualSettings&) const = default;
    };

    // Only the compositor constructs shader parameters from semantic settings.
    struct SVisualBundle {
        SVisualSettings         settings;
        bool                    blurEnabled   = true;
        int                     blurSize      = 2;
        int                     blurPasses    = 2;
        double                  glowIntensity = 0.42;
        double                  glowExtent    = 64.0;
        Render::SBlurParameters blurParameters() const;
        bool                    animated                               = true;
        double                  minimumPeriod                          = 4.9;
        double                  maximumPeriod                          = 6.4;
        double                  minimumGain                            = 0.34;
        double                  maximumGain                            = 1.38;
        bool                    operator==(const SVisualBundle&) const = default;
    };

    enum class eVisualRecovery : uint8_t {
        NONE,
        MALFORMED,
        SCHEMA,
        PRESET,
        INTENSITY,
    };

    struct SVisualResolution {
        SVisualBundle   bundle;
        eVisualRecovery recovery                                   = eVisualRecovery::NONE;
        bool            operator==(const SVisualResolution&) const = default;
    };

    struct SVisualReceiptRequest {
        std::string source;
        std::string token;
        uint64_t    serial = 0;
    };

    struct SVisualCommand {
        SVisualSettings                      settings;
        std::optional<SVisualReceiptRequest> receipt;
    };
    std::optional<SVisualCommand> parseVisualCommand(std::string_view wire);

    class CLuminophoreVisualSettings {
      public:
        CLuminophoreVisualSettings();
        static SVisualResolution resolve(const std::optional<SVisualSettings>& request);
        const SVisualResolution& apply(const std::optional<SVisualSettings>& request, const std::optional<SVisualReceiptRequest>& receipt = std::nullopt);
        const SVisualResolution& current() const;
        uint64_t                 revision() const;
        bool                     configured() const;
        std::string              json() const;
        const std::string&       source() const;
        uint64_t                 serial() const;
        static double            gainAt(const SVisualBundle& bundle, double seconds, double phase);

      private:
        SVisualResolution m_current;
        std::string       m_source;
        std::string       m_token;
        uint64_t          m_serial     = 0;
        uint64_t          m_revision   = 0;
        bool              m_configured = false;
    };
    UP<CLuminophoreVisualSettings>& visualSettings();
}
