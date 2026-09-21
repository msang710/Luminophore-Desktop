#include "LuminophoreSpatialPreviewMetrics.hpp"
#include <array>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <string_view>

using namespace Luminophore::PreviewMetrics;
struct SMetric {
    uint64_t                 count     = 0;
    uint64_t                 total     = 0;
    uint64_t                 maximum   = 0;
    std::array<uint64_t, 64> histogram = {};
};
static thread_local std::array<SMetric, static_cast<size_t>(eStage::COUNT)> metrics;
bool                                                                        Luminophore::PreviewMetrics::enabled() {
    static const bool enabled = [] {
        const auto value = std::getenv("LUMINOPHORE_SPATIAL_PREVIEW_METRICS");
        return value && std::string_view(value) == "1";
    }();
    return enabled;
}
void Luminophore::PreviewMetrics::count(eStage stage, uint64_t nanoseconds) {
    if (!enabled())
        return;
    auto& metric = metrics.at(static_cast<size_t>(stage));
    ++metric.count;
    metric.total += nanoseconds;
    metric.maximum = std::max(metric.maximum, nanoseconds);
    size_t bucket  = 0;
    for (auto value = nanoseconds; value > 1 && bucket < 63; value >>= 1)
        ++bucket;
    ++metric.histogram[bucket];
}
void Luminophore::PreviewMetrics::dump() {
    if (!enabled())
        return;
    static constexpr std::array names = {"snapshot", "copy", "transact", "projection", "prepare", "event", "cache-hit", "coalesced"};
    for (size_t i = 0; i < metrics.size(); ++i) {
        const auto& metric = metrics[i];
        std::clog << "luminophore-preview " << names[i] << " count=" << metric.count << " total_ns=" << metric.total << " max_ns=" << metric.maximum << " log2_ns=";
        for (const auto bucket : metric.histogram)
            std::clog << bucket << ',';
        std::clog << '\n';
    }
    metrics = {};
}
CTimer::CTimer(eStage stage) : m_stage(stage), m_enabled(enabled()) {
    if (m_enabled)
        m_start = std::chrono::steady_clock::now();
}
CTimer::~CTimer() {
    if (m_enabled)
        count(m_stage, std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - m_start).count());
}
