#pragma once
#include "SettingsGeneration.hpp"
#include "MonitorRuntime.hpp"
#include "InputRuntime.hpp"
#include "SettingsCommandProtocol.hpp"
#include "SettingsParticipant.hpp"
#include <chrono>
#include <future>
#include <memory>
namespace Luminophore::Settings {
    class CSettingsService {
      public:
        using Factory        = std::function<std::shared_ptr<CSettingsParticipant>(const std::string&, const SGeneration&)>;
        using MonitorFactory = std::function<SMonitorRuntime(const MonitorSettings&)>;
        using InputFactory   = std::function<SInputRuntime(const SGeneration&)>;
        CSettingsService(std::filesystem::path root, Factory factory, MonitorFactory monitors = {}, InputFactory inputs = {});
        ~CSettingsService();
        std::string request(const std::string& wire);
        void        tick();

      private:
        std::string                                          status() const;
        void                                                 initialize(const SGeneration& boot);
        bool                                                 decide(const SJointRequest& request, bool keep);
        InputFactory                                         m_inputFactory;
        SInputRuntime                                        m_inputs;
        std::string                                          m_inputValidationError;
        bool                                                 m_inputPrepared = false;
        MonitorFactory                                       m_monitorFactory;
        bool                                                 m_cancelMonitor = false;
        SMonitorRuntime                                      m_monitors;
        bool                                                 m_monitorChange   = false;
        bool                                                 m_monitorRestored = false;
        std::optional<std::chrono::steady_clock::time_point> m_confirmationSince;
        struct SRecovery {
            std::shared_ptr<CSettingsLease> lease;
            SGeneration                     generation;
            std::string                     epoch;
        };
        std::filesystem::path                 m_root;
        std::shared_ptr<CSettingsLease>       m_owner;
        Factory                               m_factory;
        std::future<SRecovery>                m_recovery;
        bool                                  m_recoveryFailed = false;
        CSettingsGenerations                  m_generations;
        MonitorSettings                       m_bootMonitors;
        std::string                           m_epoch;
        CAsyncJointSettings                   m_joint;
        std::shared_ptr<CSettingsParticipant> m_native;
        SSettingsMember                       m_member;
        std::optional<SGeneration>            m_loaded;
        std::optional<SJointRequest>          m_request;
        std::optional<SSettingsCommand>       m_observed;
        std::optional<SSettingsCommand>       m_loading;
        std::future<SGeneration>              m_read;
        std::chrono::steady_clock::time_point m_since;
    };
    std::unique_ptr<CSettingsService>& settingsService();
    void                               initSettingsService();
}
