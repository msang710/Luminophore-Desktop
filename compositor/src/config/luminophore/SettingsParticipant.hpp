#pragma once
#include "GeneratedSettings.hpp"
#include <functional>
#include <optional>
#include <string>

namespace Luminophore::Settings {
    enum class ePhase {
        IDLE,
        PREPARED,
        APPLYING,
        APPLIED,
        UNKNOWN,
        RESTORING,
        RESTORED,
        CONFIRMED
    };
    enum class eResult {
        OK,
        STALE,
        BUSY,
        INVALID,
        UNKNOWN
    };
    struct SRequest {
        std::string epoch;
        uint64_t    sequence     = 0;
        uint64_t    baseRevision = 0;
        std::string generation;
        Snapshot    values;
        bool        operator==(const SRequest&) const = default;
    };
    struct SParticipantState {
        std::string epoch;
        uint64_t    revision = 0;
        uint64_t    sequence = 0;
        ePhase      phase    = ePhase::IDLE;
        std::string confirmedGeneration;
        std::string candidateGeneration;
    };
    // Host must serialize calls on its event loop. Adapters are synchronous:
    // they cannot return/throw with a background settings write still running.
    // No disk publication or cross-process completion is implied by confirm().
    class CSettingsParticipant {
      public:
        using Apply = std::function<void(const Snapshot&)>;
        using Read  = std::function<Snapshot()>;
        CSettingsParticipant(std::string epoch, std::string generation, Snapshot initial, Apply apply, Read read, Apply restore = {});
        eResult           prepare(const SRequest& request);
        eResult           apply(const std::string& epoch, uint64_t sequence);
        eResult           restore(const std::string& epoch, uint64_t sequence);
        eResult           verify(const std::string& epoch, uint64_t sequence);
        eResult           confirm(const std::string& epoch, uint64_t sequence);
        SParticipantState state() const;

      private:
        bool                    matches(const std::string& epoch, uint64_t sequence) const;
        bool                    readMatches(const Snapshot& expected);
        std::string             m_epoch;
        std::string             m_generation;
        uint64_t                m_revision = 0;
        ePhase                  m_phase    = ePhase::IDLE;
        Snapshot                m_confirmed;
        std::optional<SRequest> m_request;
        Apply                   m_apply;
        Apply                   m_restore;
        Read                    m_read;
    };
}
