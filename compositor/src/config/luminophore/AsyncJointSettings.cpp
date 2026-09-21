#include "AsyncJointSettings.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>
using namespace Luminophore::Settings;

static bool validToken(const std::string& value, size_t size) {
    return value.size() == size && std::ranges::all_of(value, [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
}
CAsyncJointSettings::CAsyncJointSettings(std::string epoch) : m_epoch(std::move(epoch)) {
    if (!validToken(m_epoch, 32))
        throw std::invalid_argument("invalid settings epoch");
}
eJointState CAsyncJointSettings::state() const {
    return m_state;
}
std::optional<SSettingsCommand> CAsyncJointSettings::pending() const {
    return m_pending;
}

eJointState CAsyncJointSettings::start(const SJointRequest& request) {
    if (request.epoch != m_epoch || !request.sequence)
        return eJointState::STALE;
    if (!validToken(request.base, 64) || !validToken(request.candidate, 64) || request.base == request.candidate)
        return eJointState::INVALID;
    if (m_request && request.sequence == m_request->sequence)
        return request == *m_request ? m_state : eJointState::INVALID;
    if (m_request && request.sequence < m_request->sequence)
        return eJointState::STALE;
    if (m_state == eJointState::RUNNING || m_state == eJointState::UNKNOWN)
        return eJointState::BUSY;
    m_request             = request;
    m_requireConfirmation = m_awaitingConfirmation = false;
    m_touched = m_restoreRemaining = 0;
    m_prepared = m_publishAttempted = m_restoreFailed = false;
    issue(eStep::BASE);
    return m_state;
}
void CAsyncJointSettings::issue(eStep step) {
    if (m_ticket == std::numeric_limits<uint64_t>::max()) {
        m_state = eJointState::UNKNOWN;
        return;
    }
    m_step                       = step;
    eSettingsTarget    target    = eSettingsTarget::STORE;
    eSettingsOperation operation = eSettingsOperation::CURRENT;
    switch (step) {
        case eStep::BASE:
        case eStep::RESOLVE: break;
        case eStep::STORE_PREPARE:
            m_prepared = true;
            operation  = eSettingsOperation::PREPARE;
            break;
        case eStep::NATIVE_PREPARE:
            m_touched = 1;
            target    = eSettingsTarget::NATIVE;
            operation = eSettingsOperation::PREPARE;
            break;
        case eStep::SHELL_PREPARE:
            m_touched = 2;
            target    = eSettingsTarget::SHELL;
            operation = eSettingsOperation::PREPARE;
            break;
        case eStep::NATIVE_APPLY:
            target    = eSettingsTarget::NATIVE;
            operation = eSettingsOperation::APPLY;
            break;
        case eStep::SHELL_APPLY:
            target    = eSettingsTarget::SHELL;
            operation = eSettingsOperation::APPLY;
            break;
        case eStep::NATIVE_VERIFY:
        case eStep::NATIVE_FINAL_VERIFY:
            target    = eSettingsTarget::NATIVE;
            operation = eSettingsOperation::VERIFY;
            break;
        case eStep::SHELL_VERIFY:
        case eStep::SHELL_FINAL_VERIFY:
            target    = eSettingsTarget::SHELL;
            operation = eSettingsOperation::VERIFY;
            break;
        case eStep::PUBLISH:
            m_publishAttempted = true;
            operation          = eSettingsOperation::PUBLISH;
            break;
        case eStep::NATIVE_CONFIRM:
            target    = eSettingsTarget::NATIVE;
            operation = eSettingsOperation::CONFIRM;
            break;
        case eStep::SHELL_CONFIRM:
            target    = eSettingsTarget::SHELL;
            operation = eSettingsOperation::CONFIRM;
            break;
        case eStep::SHELL_RESTORE:
            target    = eSettingsTarget::SHELL;
            operation = eSettingsOperation::RESTORE;
            break;
        case eStep::NATIVE_RESTORE:
            target    = eSettingsTarget::NATIVE;
            operation = eSettingsOperation::RESTORE;
            break;
        case eStep::ABANDON: operation = eSettingsOperation::ABANDON; break;
    }
    m_pending = SSettingsCommand{*m_request, ++m_ticket, target, operation};
    m_state   = eJointState::RUNNING;
}
bool CAsyncJointSettings::timeout(const SSettingsCommand& command) {
    if (!m_pending || command != *m_pending)
        return false;
    m_state = eJointState::UNKNOWN;
    return true;
}
void CAsyncJointSettings::restoreNext() {
    if (m_restoreRemaining == 2) {
        --m_restoreRemaining;
        issue(eStep::SHELL_RESTORE);
    } else if (m_restoreRemaining == 1) {
        --m_restoreRemaining;
        issue(eStep::NATIVE_RESTORE);
    } else if (m_restoreFailed)
        m_state = eJointState::UNKNOWN;
    else if (m_prepared)
        issue(eStep::ABANDON);
    else
        m_state = eJointState::ABORTED;
}
bool CAsyncJointSettings::reply(const SSettingsCommand& command, eSettingsReply result, const std::string& current) {
    if (!m_pending || command != *m_pending)
        return false;
    if (result == eSettingsReply::UNKNOWN)
        return timeout(command);
    // A malformed successful query cannot authorize compensation/publication.
    if ((m_step == eStep::BASE || m_step == eStep::RESOLVE) && result == eSettingsReply::OK && !validToken(current, 64))
        return timeout(command);
    m_pending.reset();
    const bool ok = result == eSettingsReply::OK;
    if (m_step == eStep::SHELL_RESTORE || m_step == eStep::NATIVE_RESTORE) {
        m_restoreFailed |= !ok;
        restoreNext();
        return true;
    }
    if (!ok) {
        switch (m_step) {
            case eStep::BASE:
            case eStep::RESOLVE:
            case eStep::NATIVE_FINAL_VERIFY:
            case eStep::SHELL_FINAL_VERIFY:
            case eStep::NATIVE_CONFIRM:
            case eStep::SHELL_CONFIRM:
            case eStep::ABANDON: m_state = eJointState::UNKNOWN; break;
            default: issue(eStep::RESOLVE); break;
        }
        return true;
    }
    switch (m_step) {
        case eStep::BASE:
            if (current == m_request->base)
                issue(eStep::STORE_PREPARE);
            else
                m_state = eJointState::STALE;
            break;
        case eStep::STORE_PREPARE: issue(eStep::NATIVE_PREPARE); break;
        case eStep::NATIVE_PREPARE: issue(eStep::SHELL_PREPARE); break;
        case eStep::SHELL_PREPARE: issue(eStep::NATIVE_APPLY); break;
        case eStep::NATIVE_APPLY: issue(eStep::SHELL_APPLY); break;
        case eStep::SHELL_APPLY: issue(eStep::NATIVE_VERIFY); break;
        case eStep::NATIVE_VERIFY: issue(eStep::SHELL_VERIFY); break;
        case eStep::SHELL_VERIFY:
            if (m_requireConfirmation)
                m_awaitingConfirmation = true;
            else
                issue(eStep::PUBLISH);
            break;
        case eStep::PUBLISH: issue(eStep::RESOLVE); break;
        case eStep::RESOLVE:
            if (current == m_request->candidate && m_publishAttempted)
                issue(eStep::NATIVE_FINAL_VERIFY);
            else if (current == m_request->base) {
                m_restoreRemaining = m_touched;
                m_restoreFailed    = false;
                restoreNext();
            } else
                m_state = eJointState::UNKNOWN;
            break;
        case eStep::NATIVE_FINAL_VERIFY: issue(eStep::SHELL_FINAL_VERIFY); break;
        case eStep::SHELL_FINAL_VERIFY: issue(eStep::NATIVE_CONFIRM); break;
        case eStep::NATIVE_CONFIRM: issue(eStep::SHELL_CONFIRM); break;
        case eStep::SHELL_CONFIRM: m_state = eJointState::COMPLETE; break;
        case eStep::ABANDON: m_state = eJointState::ABORTED; break;
        default: break;
    }
    return true;
}
eJointState CAsyncJointSettings::recover(const SJointRequest& request) {
    if (!m_request || request != *m_request)
        return eJointState::STALE;
    // Still-running/lost mutation must be resolved by its receipt, not by a
    // newer checkpoint read that might race the outstanding write.
    if (m_pending || m_state != eJointState::UNKNOWN)
        return m_state;
    issue(eStep::RESOLVE);
    return m_state;
}

bool CAsyncJointSettings::requireConfirmation(const SJointRequest& request) {
    if (!m_request || request != *m_request || m_step != eStep::NATIVE_PREPARE || !m_pending)
        return false;
    m_requireConfirmation = true;
    return true;
}
bool CAsyncJointSettings::awaitingConfirmation() const {
    return m_awaitingConfirmation;
}
bool CAsyncJointSettings::decide(const SJointRequest& request, bool keep) {
    if (!m_request || request != *m_request || !m_awaitingConfirmation || m_pending)
        return false;
    m_awaitingConfirmation = false;
    issue(keep ? eStep::PUBLISH : eStep::RESOLVE);
    return true;
}
