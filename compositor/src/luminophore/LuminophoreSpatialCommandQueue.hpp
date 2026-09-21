#pragma once

#include "LuminophoreSpatialTransaction.hpp"

#include <deque>
#include <functional>
#include <vector>

class CLuminophoreSpatialCommandQueue {
  public:
    using ResultObserver = std::function<void(const SLuminophoreSpatialTransactionResult&)>;

    void                                       enqueue(LuminophoreSpatialPayload payload);
    std::vector<SLuminophoreSpatialTransactionResult> drain(CLuminophoreSpatialModel& model, const ResultObserver& observer = {});
    bool                                       draining() const;
    size_t                                     pending() const;

  private:
    std::deque<LuminophoreSpatialPayload> m_pending;
    bool                           m_draining = false;
};
