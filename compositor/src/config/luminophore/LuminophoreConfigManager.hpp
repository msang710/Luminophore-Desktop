#pragma once
#include "../ConfigManager.hpp"
#include <memory>
#include "SettingsGeneration.hpp"

namespace Luminophore::Settings {
    // Production native configuration owner for desktop and greeter.
    // Owns stable native storage; does not instantiate a Lua/legacy parser.
    class CLuminophoreConfigManager final : public Config::IConfigManager {
      public:
        CLuminophoreConfigManager();
        void                  setDeviceSettings(const DeviceSettings&) override;
        DeviceSettings        deviceSettings() const override;
        void                  setDevices(const DeviceSettings& devices);
        const DeviceSettings& devices() const;
        ~CLuminophoreConfigManager() override;
        Config::eConfigManagerType       type() override;
        void                             init() override;
        void                             reload() override;
        std::string                      verify() override;
        int                              getDeviceInt(const std::string&, const std::string&, const std::string& fallback) override;
        float                            getDeviceFloat(const std::string&, const std::string&, const std::string& fallback) override;
        Vector2D                         getDeviceVec(const std::string&, const std::string&, const std::string& fallback) override;
        std::string                      getDeviceString(const std::string&, const std::string&, const std::string& fallback) override;
        bool                             deviceConfigExplicitlySet(const std::string&, const std::string&) override;
        bool                             deviceConfigExists(const std::string&) override;
        Config::SConfigOptionReply       getConfigValue(const std::string&) override;
        std::string                      getMainConfigPath() override;
        std::string                      currentConfigPath() override;
        std::string                      getConfigString() override;
        const std::vector<std::string>&  getConfigPaths() override;
        bool                             configVerifPassed() override;
        std::string                      getErrors() override;
        std::expected<void, std::string> generateDefaultConfig(const std::filesystem::path&, bool) override;
        void                             handlePluginLoads() override;
        std::expected<void, std::string> registerPluginValue(void*, SP<Config::Values::IValue>) override;
        void                             onPluginUnload(void*) override;

      private:
        void           refreshPaths();
        const Value*   deviceValue(const std::string&, const std::string&) const;
        DeviceSettings m_devices;
        struct SStorage;
        std::unique_ptr<SStorage> m_storage;
        std::vector<std::string>  m_paths;
        std::string               m_error;
    };
}
