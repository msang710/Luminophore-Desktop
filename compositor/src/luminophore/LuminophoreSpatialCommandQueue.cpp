#include "LuminophoreSpatialCommandQueue.hpp"

void CLuminophoreSpatialCommandQueue::enqueue(LuminophoreSpatialPayload payload) {
    m_pending.emplace_back(std::move(payload));
}

std::vector<SLuminophoreSpatialTransactionResult> CLuminophoreSpatialCommandQueue::drain(CLuminophoreSpatialModel& model, const ResultObserver& observer) {
    if (m_draining)
        return {};

    m_draining = true;
    std::vector<SLuminophoreSpatialTransactionResult> results;
    while (!m_pending.empty()) {
        auto payload = std::move(m_pending.front());
        m_pending.pop_front();
        results.emplace_back(model.transact({.expectedRevision = model.revision(), .payload = std::move(payload)}));
        if (observer)
            observer(results.back());
    }
    m_draining = false;
    return results;
}

bool CLuminophoreSpatialCommandQueue::draining() const {
    return m_draining;
}

size_t CLuminophoreSpatialCommandQueue::pending() const {
    return m_pending.size();
}
