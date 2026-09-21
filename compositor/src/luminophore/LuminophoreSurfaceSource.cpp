#include "LuminophoreSurfaceSource.hpp"
#include "../protocols/core/Compositor.hpp"
#include <atomic>
#include <cmath>

using namespace Luminophore;
static std::atomic<uint64_t> g_nextSourceToken = 1;

CLuminophoreSurfaceSource::CLuminophoreSurfaceSource(const SP<CWLSurfaceResource>& surface) : m_surface(surface), m_token(g_nextSourceToken.fetch_add(1)) {
    m_lastExtent = describe(m_token, m_revision, true, true, !!surface->m_current.buffer, surface->m_current).extent;
    m_commit     = surface->m_events.commit.listen([this] {
        const auto current = m_surface.lock();
        if (!current || m_destroyed)
            return;
        ++m_revision;
        const auto extent = describe(m_token, m_revision, true, true, !!current->m_current.buffer, current->m_current).extent;
        if (extent != m_lastExtent) {
            m_lastExtent = extent;
            ++m_extentRevision;
        }
    });
    m_destroy    = surface->m_events.destroy.listen([this] {
        if (m_destroyed)
            return;
        m_destroyed = true;
        m_surface.reset();
        ++m_revision;
    });
}

SP<CLuminophoreSurfaceSource> CLuminophoreSurfaceSource::create(const SP<CWLSurfaceResource>& surface) {
    if (!surface)
        return nullptr;
    if (surface->m_luminophoreSource)
        return surface->m_luminophoreSource;
    return SP<CLuminophoreSurfaceSource>(new CLuminophoreSurfaceSource(surface));
}

SP<CWLSurfaceResource> CLuminophoreSurfaceSource::surface() const {
    return m_destroyed ? nullptr : m_surface.lock();
}

SSurfaceSourceSnapshot CLuminophoreSurfaceSource::describe(uint64_t token, uint64_t revision, bool alive, bool mapped, bool hasBuffer, const SSurfaceState& committed,
                                                    uint64_t extentRevision) {
    SSurfaceSourceSnapshot result{
        .token = token, .revision = revision, .extentRevision = extentRevision, .alive = alive, .mapped = alive && mapped, .hasBuffer = alive && hasBuffer};
    if (!token || !alive || !mapped || !hasBuffer || committed.scale <= 0 || committed.transform < 0 || committed.transform > 7)
        return result;
    // Derive from committed buffer/viewport metadata. A viewport-only commit can
    // change the logical extent without replacing the buffer (or cached size).
    auto width  = committed.bufferSize.x;
    auto height = committed.bufferSize.y;
    if (!std::isfinite(width) || !std::isfinite(height) || width <= 0 || height <= 0)
        return result;
    if (committed.transform % 2)
        std::swap(width, height);
    width /= committed.scale;
    height /= committed.scale;
    if (committed.viewport.hasSource) {
        const auto& box = committed.viewport.source;
        if (!std::isfinite(box.x) || !std::isfinite(box.y) || !std::isfinite(box.w) || !std::isfinite(box.h) || box.x < 0 || box.y < 0 || box.w <= 0 || box.h <= 0 ||
            box.x + box.w > width || box.y + box.h > height)
            return result;
        width  = box.w;
        height = box.h;
    }
    if (committed.viewport.hasDestination) {
        width  = committed.viewport.destination.x;
        height = committed.viewport.destination.y;
    }
    if (std::isfinite(width) && std::isfinite(height) && width > 0 && height > 0)
        result.extent = SSourceExtent{.width = width, .height = height};
    return result;
}

SSurfaceSourceSnapshot CLuminophoreSurfaceSource::snapshot() const {
    const auto current = surface();
    if (!current)
        return {.token = m_token, .revision = m_revision, .extentRevision = m_extentRevision};
    return describe(m_token, m_revision, true, current->m_mapped, !!current->m_current.buffer, current->m_current, m_extentRevision);
}
