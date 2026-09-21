#pragma once
#include "SettingsGeneration.hpp"
#include <functional>
#include <memory>
#include <string>
#include <string_view>

namespace Luminophore::Settings {
    struct SLuaSettingsBackend {
        std::function<void()> apply, verify, restore;
    };
    class CLuaSettingsTransactions {
      public:
        using Factory = std::function<SLuaSettingsBackend(const SGeneration&, const SGeneration&)>;
        explicit CLuaSettingsTransactions(Factory factory);
        std::string request(std::string_view wire);

      private:
        Factory             m_factory;
        std::string         m_identity, m_payload, m_phase;
        SLuaSettingsBackend m_backend;
    };
    std::string luaSettingsRequest(std::string_view wire);
}
