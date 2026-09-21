#include "LuminophoreSpatialPreview.hpp"
bool CLuminophoreSpatialPreviewCache::matches(const SLuminophoreSpatialPreviewKey& key) const {
    return m_last && *m_last == key;
}
void CLuminophoreSpatialPreviewCache::remember(const SLuminophoreSpatialPreviewKey& key) {
    m_last = key;
}
void CLuminophoreSpatialPreviewCache::clear() {
    m_last.reset();
}
std::optional<uint64_t> CLuminophoreSpatialPreviewQueue::push(double x, double y) {
    m_latest = {x, y};
    if (m_pending)
        return std::nullopt;
    m_pending = true;
    return ++m_token;
}
std::optional<std::pair<double, double>> CLuminophoreSpatialPreviewQueue::take(uint64_t token) {
    if (!m_pending || token != m_token)
        return std::nullopt;
    m_pending = false;
    return m_latest;
}
void CLuminophoreSpatialPreviewQueue::cancel() {
    ++m_token;
    m_pending = false;
}
