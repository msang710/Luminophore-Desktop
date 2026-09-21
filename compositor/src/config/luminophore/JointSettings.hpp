#pragma once
#include <cstdint>
#include <functional>
#include <optional>
#include <string>

namespace Luminophore::Settings {
    struct SJointRequest {
        std::string epoch;
        uint64_t    sequence = 0;
        std::string base;
        std::string candidate;
        bool        operator==(const SJointRequest&) const = default;
    };
    enum class eJointState {
        IDLE,
        RUNNING,
        COMPLETE,
        ABORTED,
        UNKNOWN,
        STALE,
        BUSY,
        INVALID
    };
    // All hooks must finish or throw without leaving outstanding writes.
    // Local participant hooks are driven by CAsyncJointSettings commands.
    // Never block the compositor event loop on Shell IPC.
    struct SSettingsMember {
        std::function<void(const SJointRequest&)> prepare;
        std::function<void(const SJointRequest&)> apply;
        std::function<void(const SJointRequest&)> verify;
        // Restore must safely abandon a prepared-but-not-applied request too.
        std::function<void(const SJointRequest&)> restore;
        std::function<void(const SJointRequest&)> confirm;
    };
}
