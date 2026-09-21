#pragma once
#include <functional>
namespace Luminophore::Settings {
    struct SGeneration;
    struct SInputRuntime {
        std::function<void(const SGeneration&)> validate, prepare;
        std::function<void()>                   apply, verify, restore;
    };
    void          validateInputGeneration(const SGeneration&);
    SInputRuntime makeInputRuntime(const SGeneration&);
}
