#include "SettingsService.hpp"
#include "JointSettingsMember.hpp"
#include <uuid/uuid.h>
#include <sstream>
#include <format>
#include <stdexcept>
using namespace Luminophore::Settings;
static std::string newEpoch() {
    uuid_t id;
    uuid_generate_random(id);
    char text[37];
    uuid_unparse_lower(id, text);
    std::string value;
    for (const char c : std::string(text))
        if (c != '-')
            value += c;
    return value;
}
static std::string stateName(eJointState state) {
    switch (state) {
        case eJointState::IDLE: return "idle";
        case eJointState::RUNNING: return "running";
        case eJointState::COMPLETE: return "complete";
        case eJointState::ABORTED: return "aborted";
        case eJointState::UNKNOWN: return "unknown";
        case eJointState::STALE: return "stale";
        case eJointState::BUSY: return "busy";
        default: return "invalid";
    }
}
CSettingsService::CSettingsService(std::filesystem::path root, Factory factory, MonitorFactory monitors, InputFactory inputs) :
    m_inputFactory(std::move(inputs)), m_monitorFactory(std::move(monitors)), m_root(root), m_owner(std::make_shared<CSettingsLease>(root / "coordinator.lock")),
    m_factory(std::move(factory)), m_generations(root), m_epoch(newEpoch()), m_joint(m_epoch) {
    m_generations.bootstrap();
    initialize(m_generations.recover(m_epoch));
}
CSettingsService::~CSettingsService() {
    // Async jobs capture this; join before generations and leases are destroyed.
    if (m_read.valid())
        m_read.wait();
    if (m_recovery.valid())
        m_recovery.wait();
}
void CSettingsService::initialize(const SGeneration& boot) {
    m_inputs        = m_inputFactory ? m_inputFactory(boot) : SInputRuntime{};
    m_inputPrepared = false;
    m_monitors      = m_monitorFactory ? m_monitorFactory(boot.monitors) : SMonitorRuntime{};
    m_monitorChange = m_monitorRestored = m_cancelMonitor = false;
    m_confirmationSince.reset();
    auto native    = m_factory(m_epoch, boot);
    m_bootMonitors = boot.monitors;
    m_native       = std::move(native);
    m_joint        = CAsyncJointSettings(m_epoch);
    m_request.reset();
    m_observed.reset();
    m_loading.reset();
    m_loaded.reset();
    m_member = makeJointMember(m_native, [this](const std::string& id) {
        if (!m_loaded || m_loaded->id != id)
            throw std::runtime_error("candidate not loaded");
        return m_loaded->values;
    });
}
void CSettingsService::tick() {
    if (m_recovery.valid()) {
        if (m_recovery.wait_for(std::chrono::seconds(0)) != std::future_status::ready)
            return;
        try {
            auto recovery = m_recovery.get();
            m_epoch       = recovery.epoch;
            initialize(recovery.generation);
            m_recoveryFailed = false;
        } catch (...) { m_recoveryFailed = true; }
        return;
    }
    if (m_recoveryFailed)
        return;
    if (m_monitorChange && m_confirmationSince && !m_monitorRestored &&
        (std::chrono::steady_clock::now() - *m_confirmationSince >= std::chrono::seconds(15) || !m_monitors.healthy())) {
        m_cancelMonitor = true;
        try {
            m_monitors.restore();
            m_monitorRestored = true;
        } catch (...) { ; }
    }
    if (m_joint.awaitingConfirmation()) {
        if (!m_confirmationSince)
            m_confirmationSince = std::chrono::steady_clock::now();
        if (std::chrono::steady_clock::now() - *m_confirmationSince >= std::chrono::seconds(15) || m_cancelMonitor || !m_monitors.healthy())
            decide(*m_request, false);
        else
            return;
    }
    for (unsigned step = 0; step < 8; ++step) {
        const auto command = m_joint.pending();
        if (!command)
            return;
        if (command != m_observed) {
            m_observed = command;
            m_since    = std::chrono::steady_clock::now();
        }
        if (std::chrono::steady_clock::now() - m_since > std::chrono::seconds(5))
            m_joint.timeout(*command);
        if (command->target != eSettingsTarget::NATIVE)
            return;
        if (command->operation == eSettingsOperation::PREPARE) {
            if (!m_loading) {
                m_loading     = command;
                const auto id = command->request.candidate;
                m_read        = std::async(std::launch::async, [this, id] {
                    auto generation = m_generations.load(id);
                    m_inputValidationError.clear();
                    try {
                        if (m_inputs.validate)
                            m_inputs.validate(generation);
                    } catch (const std::exception& error) { m_inputValidationError = error.what(); }
                    return generation;
                });
            }
            if (m_read.wait_for(std::chrono::seconds(0)) != std::future_status::ready)
                return;
            try {
                m_loaded = m_read.get();
            } catch (...) {
                m_loading.reset();
                m_joint.reply(*command, eSettingsReply::FAILED);
                continue;
            }
            m_loading.reset();
        }
        auto result = executeMember(*command, m_member);
        if (result.result == eSettingsReply::OK) {
            try {
                if (command->operation == eSettingsOperation::PREPARE) {
                    m_inputPrepared   = false;
                    m_monitorChange   = false;
                    m_monitorRestored = m_cancelMonitor = false;
                    m_confirmationSince.reset();
                    if (!m_inputValidationError.empty())
                        throw std::runtime_error(m_inputValidationError);
                    if (m_inputs.prepare) {
                        m_inputs.prepare(*m_loaded);
                        m_inputPrepared = true;
                    } else if (!m_loaded->devices.empty())
                        throw std::runtime_error("device participant unavailable");
                    m_monitorChange = m_loaded && m_loaded->monitors != m_bootMonitors;
                    if (m_monitorChange) {
                        if (!m_monitors.prepare)
                            throw std::runtime_error("monitor participant unavailable");
                        m_monitors.prepare(m_loaded->monitors);
                        if (!m_joint.requireConfirmation(command->request))
                            throw std::runtime_error("confirmation gate unavailable");
                    }
                } else if (m_monitorChange) {
                    switch (command->operation) {
                        case eSettingsOperation::APPLY:
                            m_confirmationSince = std::chrono::steady_clock::now();
                            m_monitors.apply();
                            break;
                        case eSettingsOperation::VERIFY:
                            if (m_cancelMonitor)
                                throw std::runtime_error("monitor confirmation expired");
                            m_monitors.verify();
                            break;
                        case eSettingsOperation::RESTORE:
                            if (!m_monitorRestored && m_monitors.restore)
                                m_monitors.restore();
                            m_monitorRestored = true;
                            break;
                        case eSettingsOperation::CONFIRM: m_bootMonitors = m_loaded->monitors; break;
                        default: break;
                    }
                }
                if (m_inputPrepared) {
                    if (command->operation == eSettingsOperation::APPLY)
                        m_inputs.apply();
                    else if (command->operation == eSettingsOperation::VERIFY)
                        m_inputs.verify();
                    else if (command->operation == eSettingsOperation::RESTORE)
                        m_inputs.restore();
                }
            } catch (...) { result.result = eSettingsReply::FAILED; }
        }
        m_joint.reply(*command, result.result, result.current);
    }
}
std::string CSettingsService::status() const {
    const auto pending = m_joint.pending();
    const auto state   = m_native->state();
    return std::format(
        R"({{"version":1,"epoch":"{}","state":"{}","sequence":"{}","base":"{}","candidate":"{}","confirmed":"{}","command":"{}","recovering":{},"recoveryFailed":{},"awaitingConfirmation":{}}})",
        m_epoch, stateName(m_joint.state()), m_request ? std::to_string(m_request->sequence) : "0", m_request ? m_request->base : state.confirmedGeneration,
        m_request ? m_request->candidate : "", state.confirmedGeneration, pending && pending->target != eSettingsTarget::NATIVE ? commandWire(*pending) : "",
        m_recovery.valid() ? "true" : "false", m_recoveryFailed ? "true" : "false", m_joint.awaitingConfirmation() ? "true" : "false");
}
std::string CSettingsService::request(const std::string& wire) {
    if (wire.size() > 1024 || wire.find_first_of("\r\n\t") != std::string::npos)
        return R"({"error":"invalid request"})";
    tick();
    if (wire == "status")
        return status();
    if (wire == "attach") {
        // Only a released process-lifetime lease proves the old participant and
        // its in-process disk worker are gone/quiescent. Disconnect is not proof.
        if (!m_recovery.valid()) {
            if (m_read.valid())
                return R"({"error":"native read pending"})";
            std::shared_ptr<CSettingsLease> lease;
            try {
                lease = std::make_shared<CSettingsLease>(m_root / "shell.lock");
            } catch (...) { return R"({"error":"owner active"})"; }
            m_recovery = std::async(std::launch::async, [this, lease] {
                const auto epoch = newEpoch();
                return SRecovery{lease, m_generations.recover(epoch), epoch};
            });
        }
        return status();
    }
    if (m_recovery.valid() || m_recoveryFailed)
        return R"({"error":"recovery pending"})";
    if (wire.starts_with("receipt ")) {
        const auto command = m_joint.pending();
        if (!command || command->target == eSettingsTarget::NATIVE)
            return R"({"error":"stale receipt"})";
        const auto response = parseReceipt(*command, std::string_view(wire).substr(8));
        if (!response)
            return R"({"error":"invalid receipt"})";
        m_joint.reply(*command, response->result, response->current);
        tick();
        return status();
    }
    std::istringstream stream(wire);
    std::string        op, sequence, extra;
    SJointRequest      r;
    if (!(stream >> op >> r.epoch >> sequence >> r.base >> r.candidate) || stream >> extra || sequence.empty() || sequence[0] == '0' ||
        sequence.find_first_not_of("0123456789") != std::string::npos)
        return R"({"error":"invalid request"})";
    try {
        r.sequence = std::stoull(sequence);
    } catch (...) { return R"({"error":"invalid sequence"})"; }
    if (op == "start") {
        const auto result = m_joint.start(r);
        if (result != eJointState::RUNNING && result != eJointState::COMPLETE && result != eJointState::UNKNOWN)
            return std::format(R"({{"error":"{}"}})", stateName(result));
        m_request = r;
    } else if (op == "keep" || op == "cancel") {
        if (!decide(r, op == "keep"))
            return R"({"error":"stale confirmation"})";
    } else if (op == "recover") {
        if (!m_request || r != *m_request)
            return R"({"error":"stale request"})";
        m_joint.recover(r);
    } else
        return R"({"error":"invalid operation"})";
    tick();
    return status();
}
bool CSettingsService::decide(const SJointRequest& request, bool keep) {
    if (!m_request || request != *m_request || !m_joint.awaitingConfirmation())
        return false;
    if (keep) {
        try {
            m_monitors.verify();
        } catch (...) { keep = false; }
    }
    if (!keep) {
        // No publication command exists while awaiting confirmation. Restore
        // the display locally even when the Shell/checkpoint worker is gone.
        try {
            m_monitors.restore();
            m_monitorRestored = true;
        } catch (...) { m_monitorRestored = false; }
    }
    m_confirmationSince.reset();
    return m_joint.decide(request, keep);
}
static std::unique_ptr<CSettingsService> g_service;
std::unique_ptr<CSettingsService>&       Luminophore::Settings::settingsService() {
    return g_service;
}
