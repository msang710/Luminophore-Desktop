#include "LuminophoreEffectConfig.hpp"

using namespace Render;

const SLuminophoreEffectConfig& Render::luminophoreEffectConfig() {
    static const SLuminophoreEffectConfig CONFIG;
    return CONFIG;
}
