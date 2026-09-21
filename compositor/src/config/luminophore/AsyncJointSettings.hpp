#pragma once
#include "JointSettings.hpp"

namespace Luminophore::Settings {
    enum class eSettingsTarget {
        STORE,
        NATIVE,
        SHELL
    };
    enum class eSettingsOperation {
        CURRENT,
        PREPARE,
        APPLY,
        VERIFY,
        PUBLISH,
        RESTORE,
        CONFIRM,
        ABANDON
    };
    enum class eSettingsReply {
        OK,
        FAILED,
        UNKNOWN
    };
    struct SSettingsCommand {
        SJointRequest      request;
        uint64_t           ticket                                    = 0;
        eSettingsTarget    target                                    = eSettingsTarget::STORE;
        eSettingsOperation operation                                 = eSettingsOperation::CURRENT;
        bool               operator==(const SSettingsCommand&) const = default;
    };
    // Event-loop state machine: no I/O, waits, worker threads or callbacks.
    // Host dispatches pending() once, then returns its terminal receipt to reply().
    // FAILED means the operation stopped with no remaining background writes;
    // transport timeout/disconnect is UNKNOWN, never FAILED. UNKNOWN commands
    // stay pending until an authoritative receipt resolves the same identity.
    class CAsyncJointSettings {
      public:
        explicit CAsyncJointSettings(std::string epoch);
        eJointState                     start(const SJointRequest& request);
        std::optional<SSettingsCommand> pending() const;
        bool                            reply(const SSettingsCommand& command, eSettingsReply result, const std::string& current = "");
        bool                            timeout(const SSettingsCommand& command);
        eJointState                     recover(const SJointRequest& request);
        eJointState                     state() const;
        bool                            requireConfirmation(const SJointRequest& request);
        bool                            awaitingConfirmation() const;
        bool                            decide(const SJointRequest& request, bool keep);

      private:
        enum class eStep {
            BASE,
            STORE_PREPARE,
            NATIVE_PREPARE,
            SHELL_PREPARE,
            NATIVE_APPLY,
            SHELL_APPLY,
            NATIVE_VERIFY,
            SHELL_VERIFY,
            PUBLISH,
            RESOLVE,
            NATIVE_FINAL_VERIFY,
            SHELL_FINAL_VERIFY,
            NATIVE_CONFIRM,
            SHELL_CONFIRM,
            SHELL_RESTORE,
            NATIVE_RESTORE,
            ABANDON
        };
        void                            issue(eStep step);
        void                            restoreNext();
        std::string                     m_epoch;
        std::optional<SJointRequest>    m_request;
        std::optional<SSettingsCommand> m_pending;
        uint64_t                        m_ticket               = 0;
        eStep                           m_step                 = eStep::BASE;
        eJointState                     m_state                = eJointState::IDLE;
        unsigned                        m_touched              = 0;
        unsigned                        m_restoreRemaining     = 0;
        bool                            m_requireConfirmation  = false;
        bool                            m_awaitingConfirmation = false;
        bool                            m_prepared             = false;
        bool                            m_publishAttempted     = false;
        bool                            m_restoreFailed        = false;
    };
}
