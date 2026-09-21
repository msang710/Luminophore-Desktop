#pragma once
#include "SettingsParticipant.hpp"
#include <functional>
#include <memory>
#include <vector>

namespace Luminophore::Settings {
    struct SRuntimeSlot {
        std::function<Value()>            read;
        std::function<void(const Value&)> write;
        // Availability/type check only. Must not compare against the last
        // applied value: restore must be able to repair drift.
        std::function<void()> writable = {};
        // Validate candidate capability before any destination is modified.
        std::function<void(const Value&)> validate = {};
    };
    // Temporary consumer bridge for the complete generated scalar schema.
    // No startup or mutation endpoint enables it.
    class CRuntimeSettingsAdapter {
      public:
        CRuntimeSettingsAdapter(Snapshot initial, std::map<std::string, SRuntimeSlot> slots, std::function<void()> refresh, std::function<void()> restoreRefresh = {});
        Snapshot                               read() const;
        void                                   apply(const Snapshot& candidate);
        void                                   restore(const Snapshot& candidate);
        static const std::vector<std::string>& supported();

      private:
        void                                applyValues(const Snapshot& candidate, bool restoring);
        Snapshot                            m_canonical;
        std::map<std::string, SRuntimeSlot> m_slots;
        std::function<void()>               m_refresh;
        std::function<void()>               m_restoreRefresh;
    };
    std::shared_ptr<CRuntimeSettingsAdapter> makeRuntimeAdapter(const Snapshot& initial, bool initialize = false);
    std::unique_ptr<CSettingsParticipant>    makeRuntimeParticipant(const std::string& epoch, const std::string& generation, const Snapshot& initial);
}
