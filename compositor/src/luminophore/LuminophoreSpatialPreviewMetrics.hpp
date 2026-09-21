#pragma once
#include <chrono>
#include <cstdint>

namespace Luminophore::PreviewMetrics {
    enum class eStage : uint8_t {
        SNAPSHOT,
        COPY,
        TRANSACT,
        PROJECTION,
        PREPARE,
        EVENT,
        CACHE_HIT,
        COALESCED,
        COUNT
    };
    bool enabled();
    void count(eStage stage, uint64_t nanoseconds = 0);
    void dump();
    class CTimer {
      public:
        explicit CTimer(eStage stage);
        ~CTimer();

      private:
        eStage                                m_stage;
        bool                                  m_enabled = false;
        std::chrono::steady_clock::time_point m_start;
    };
}
