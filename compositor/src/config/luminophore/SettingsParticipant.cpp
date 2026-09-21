#include "SettingsParticipant.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

static bool hexToken(const std::string& text, size_t length) {
    return text.size() == length && std::ranges::all_of(text, [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
}
static bool fullSnapshot(const Luminophore::Settings::Snapshot& values) {
    return values.size() == Luminophore::Settings::defaults().size() && Luminophore::Settings::validate(values).empty();
}

using namespace Luminophore::Settings;

CSettingsParticipant::CSettingsParticipant(std::string epoch, std::string generation, Snapshot initial, Apply apply, Read read, Apply restore) :
    m_epoch(std::move(epoch)), m_generation(std::move(generation)), m_confirmed(std::move(initial)), m_apply(std::move(apply)), m_restore(restore ? std::move(restore) : m_apply),
    m_read(std::move(read)) {
    if (!hexToken(m_epoch, 32) || !hexToken(m_generation, 64) || !fullSnapshot(m_confirmed) || !m_apply || !m_read)
        throw std::invalid_argument("invalid settings participant initialization");
}

bool CSettingsParticipant::matches(const std::string& epoch, uint64_t sequence) const {
    return m_request && epoch == m_epoch && sequence == m_request->sequence;
}

bool CSettingsParticipant::readMatches(const Snapshot& expected) {
    try {
        return m_read() == expected;
    } catch (...) { return false; }
}

eResult CSettingsParticipant::prepare(const SRequest& request) {
    if (request.epoch != m_epoch || request.sequence == 0)
        return eResult::STALE;
    if (m_phase == ePhase::APPLYING || m_phase == ePhase::RESTORING)
        return eResult::BUSY;
    if (m_request && request.sequence == m_request->sequence)
        return request == *m_request ? (m_phase == ePhase::UNKNOWN ? eResult::UNKNOWN : eResult::OK) : eResult::INVALID;
    if (m_request && request.sequence < m_request->sequence)
        return eResult::STALE;
    if (m_phase != ePhase::IDLE && m_phase != ePhase::RESTORED && m_phase != ePhase::CONFIRMED)
        return eResult::BUSY;
    if (request.baseRevision != m_revision || m_revision == std::numeric_limits<uint64_t>::max())
        return eResult::STALE;
    if (!hexToken(request.generation, 64) || !fullSnapshot(request.values) || (request.generation == m_generation && request.values != m_confirmed))
        return eResult::INVALID;
    m_request = request;
    // Fence callback reentry even during a read. Unexpected external mutation
    // requires explicit restoration instead of accepting an inaccurate base.
    m_phase = ePhase::APPLYING;
    if (!readMatches(m_confirmed)) {
        m_phase = ePhase::UNKNOWN;
        return eResult::UNKNOWN;
    }
    m_phase = ePhase::PREPARED;
    return eResult::OK;
}

eResult CSettingsParticipant::apply(const std::string& epoch, uint64_t sequence) {
    if (!matches(epoch, sequence))
        return eResult::STALE;
    if (m_phase == ePhase::APPLIED || m_phase == ePhase::CONFIRMED)
        return eResult::OK;
    if (m_phase == ePhase::UNKNOWN)
        return eResult::UNKNOWN;
    if (m_phase != ePhase::PREPARED)
        return eResult::BUSY;
    m_phase = ePhase::APPLYING;
    try {
        m_apply(m_request->values);
    } catch (...) {
        m_phase = ePhase::UNKNOWN;
        return eResult::UNKNOWN;
    }
    m_phase = readMatches(m_request->values) ? ePhase::APPLIED : ePhase::UNKNOWN;
    return m_phase == ePhase::APPLIED ? eResult::OK : eResult::UNKNOWN;
}

eResult CSettingsParticipant::restore(const std::string& epoch, uint64_t sequence) {
    if (!matches(epoch, sequence))
        return eResult::STALE;
    if (m_phase == ePhase::RESTORED)
        return eResult::OK;
    if (m_phase == ePhase::CONFIRMED || m_phase == ePhase::APPLYING || m_phase == ePhase::RESTORING)
        return eResult::BUSY;
    m_phase = ePhase::RESTORING;
    try {
        m_restore(m_confirmed);
    } catch (...) {
        m_phase = ePhase::UNKNOWN;
        return eResult::UNKNOWN;
    }
    if (!readMatches(m_confirmed)) {
        m_phase = ePhase::UNKNOWN;
        return eResult::UNKNOWN;
    }
    ++m_revision;
    m_phase = ePhase::RESTORED;
    return eResult::OK;
}

eResult CSettingsParticipant::verify(const std::string& epoch, uint64_t sequence) {
    if (!matches(epoch, sequence))
        return eResult::STALE;
    if (m_phase != ePhase::APPLIED && m_phase != ePhase::CONFIRMED)
        return m_phase == ePhase::UNKNOWN ? eResult::UNKNOWN : eResult::BUSY;
    const auto previous = m_phase;
    m_phase             = ePhase::APPLYING;
    const bool matched  = readMatches(m_request->values);
    // A read failure alone must remain retryable without another write.
    m_phase = previous;
    return matched ? eResult::OK : eResult::UNKNOWN;
}

eResult CSettingsParticipant::confirm(const std::string& epoch, uint64_t sequence) {
    if (!matches(epoch, sequence))
        return eResult::STALE;
    if (m_phase == ePhase::CONFIRMED)
        return eResult::OK;
    if (m_phase != ePhase::APPLIED)
        return m_phase == ePhase::UNKNOWN ? eResult::UNKNOWN : eResult::BUSY;
    m_phase = ePhase::APPLYING;
    if (!readMatches(m_request->values)) {
        m_phase = ePhase::APPLIED;
        return eResult::UNKNOWN;
    }
    m_confirmed  = m_request->values;
    m_generation = m_request->generation;
    ++m_revision;
    m_phase = ePhase::CONFIRMED;
    return eResult::OK;
}

SParticipantState CSettingsParticipant::state() const {
    return {m_epoch, m_revision, m_request ? m_request->sequence : 0, m_phase, m_generation, m_request ? m_request->generation : ""};
}
