#pragma once
#include "MonitorSettings.hpp"
#include <functional>

namespace Luminophore::Settings {
    // Synchronous native operations: no outstanding modeset after returning.
    struct SMonitorRuntime {
        std::function<void(const MonitorSettings&)> prepare;
        std::function<void()>                       apply;
        std::function<void()>                       verify;
        std::function<void()>                       restore;
        std::function<bool()>                       healthy;
    };
    SMonitorRuntime makeMonitorRuntime(const MonitorSettings& boot);
}
