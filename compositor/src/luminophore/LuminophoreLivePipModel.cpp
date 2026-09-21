#include "LuminophoreLivePipModel.hpp"
#include <cmath>
#include <limits>

using namespace Luminophore;

bool CLuminophoreLivePipModel::validRect(const SLivePipRect& rect) {
    return std::isfinite(rect.x) && std::isfinite(rect.y) && std::isfinite(rect.width) && std::isfinite(rect.height) && rect.width > 0 && rect.height > 0 &&
        std::isfinite(rect.x + rect.width) && std::isfinite(rect.y + rect.height) && rect.x + rect.width > rect.x && rect.y + rect.height > rect.y;
}

bool CLuminophoreLivePipModel::sampleable(const SLivePipEntry& entry, const SSurfaceSourceSnapshot& source) {
    if (!source.token || source.token != entry.sourceToken || !source.alive || !source.mapped || !source.hasBuffer || !source.extent || !validRect(entry.crop))
        return false;
    const auto& extent = *source.extent;
    return std::isfinite(extent.width) && std::isfinite(extent.height) && entry.crop.x >= 0 && entry.crop.y >= 0 && entry.crop.x + entry.crop.width <= extent.width &&
        entry.crop.y + entry.crop.height <= extent.height;
}

std::optional<uint64_t> CLuminophoreLivePipModel::create(const SSurfaceSourceSnapshot& source, uint64_t expectedExtentRevision, SLivePipRect crop, const std::string& output,
                                                  SLivePipRect destination) {
    SLivePipEntry entry{.id = m_nextID, .sourceToken = source.token, .sourceRevision = source.revision, .crop = crop, .output = output, .destination = destination};
    if (!source.revision || !source.extentRevision || source.extentRevision != expectedExtentRevision || !sampleable(entry, source) || output.empty() || !validRect(destination) ||
        m_nextID == std::numeric_limits<uint64_t>::max() || m_revision == std::numeric_limits<uint64_t>::max())
        return std::nullopt;
    m_entries.emplace(entry.id, entry);
    ++m_nextID;
    ++m_revision;
    return entry.id;
}

bool CLuminophoreLivePipModel::remove(uint64_t id) {
    if (m_revision == std::numeric_limits<uint64_t>::max() || !m_entries.erase(id))
        return false;
    ++m_revision;
    return true;
}

const std::map<uint64_t, SLivePipEntry>& CLuminophoreLivePipModel::entries() const {
    return m_entries;
}

uint64_t CLuminophoreLivePipModel::revision() const {
    return m_revision;
}

bool CLuminophoreLivePipModel::update(uint64_t id, uint64_t expectedRevision, const SSurfaceSourceSnapshot& source, uint64_t expectedExtentRevision, SLivePipRect crop,
                               const std::string& output, SLivePipRect destination) {
    const auto it = m_entries.find(id);
    if (expectedRevision != m_revision || it == m_entries.end() || m_revision == std::numeric_limits<uint64_t>::max() || it->second.sourceToken != source.token ||
        !source.extentRevision || source.extentRevision != expectedExtentRevision || output.empty() || !validRect(destination))
        return false;
    auto candidate        = it->second;
    candidate.crop        = crop;
    candidate.output      = output;
    candidate.destination = destination;
    if (!sampleable(candidate, source))
        return false;
    candidate.sourceRevision = source.revision;
    it->second               = candidate;
    ++m_revision;
    return true;
}

bool CLuminophoreLivePipModel::place(uint64_t id, uint64_t expectedRevision, const std::string& output, SLivePipRect destination) {
    const auto it = m_entries.find(id);
    if (expectedRevision != m_revision || it == m_entries.end() || output.empty() || !validRect(destination) || m_revision == std::numeric_limits<uint64_t>::max())
        return false;
    if (it->second.output == output && it->second.destination == destination)
        return true;
    it->second.output      = output;
    it->second.destination = destination;
    ++m_revision;
    return true;
}
