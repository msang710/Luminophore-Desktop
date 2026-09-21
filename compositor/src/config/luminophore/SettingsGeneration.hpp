#pragma once
#include "GeneratedSettings.hpp"
#include "MonitorSettings.hpp"
#include <array>
#include <filesystem>
#include <string>
#include <memory>

namespace Luminophore::Settings {
    class CSettingsLease {
      public:
        explicit CSettingsLease(const std::filesystem::path& path);
        ~CSettingsLease();
        CSettingsLease(const CSettingsLease&)            = delete;
        CSettingsLease& operator=(const CSettingsLease&) = delete;

      private:
        int m_fd = -1;
    };
    using DeviceSettings = std::map<std::string, Snapshot>;
    struct SGeneration {
        std::string                id;
        Snapshot                   values;
        int                        panelHeight = 44;
        MonitorSettings            monitors;
        DeviceSettings             devices;
        std::array<std::string, 5> documents;
    };
    // Native immutable generation storage shared by desktop and greeter.
    class CSettingsGenerations {
      public:
        explicit CSettingsGenerations(std::filesystem::path root);
        SGeneration load(const std::string& id) const;
        SGeneration completed() const;
        SGeneration bootstrap() const;
        SGeneration recover(const std::string& epoch) const;

      private:
        std::filesystem::path m_root;
    };
    void                  validateGenerationInputs(const SGeneration& generation);
    void                  selectSafeSettings(const std::string& reason);
    bool                  settingsFixtureEnabled();
    std::filesystem::path settingsFixtureRoot();
}
