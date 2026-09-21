#pragma once

#include "LuminophoreSurfaceSource.hpp"
#include <map>
#include <string>

namespace Luminophore {
    struct SLivePipRect {
        double x                                     = 0;
        double y                                     = 0;
        double width                                 = 0;
        double height                                = 0;
        bool   operator==(const SLivePipRect&) const = default;
    };

    struct SLivePipEntry {
        uint64_t     id             = 0;
        uint64_t     sourceToken    = 0;
        uint64_t     sourceRevision = 0;
        SLivePipRect crop;
        std::string  output;
        SLivePipRect destination;
    };

    // No board/anchor/mesh/history ownership. Source loss or actual resize does
    // not silently delete or change the selection: later lifecycle policy owns it.
    class CLuminophoreLivePipModel {
      public:
        std::optional<uint64_t> create(const SSurfaceSourceSnapshot& source, uint64_t expectedExtentRevision, SLivePipRect crop, const std::string& output,
                                       SLivePipRect destination);
        bool                    remove(uint64_t id);
        bool update(uint64_t id, uint64_t expectedRevision, const SSurfaceSourceSnapshot& source, uint64_t expectedExtentRevision, SLivePipRect crop, const std::string& output,
                    SLivePipRect destination);
        const std::map<uint64_t, SLivePipEntry>& entries() const;
        uint64_t                                 revision() const;
        static bool                              validRect(const SLivePipRect& rect);
        static bool                              sampleable(const SLivePipEntry& entry, const SSurfaceSourceSnapshot& source);

        bool                                     place(uint64_t id, uint64_t expectedRevision, const std::string& output, SLivePipRect destination);

      private:
        std::map<uint64_t, SLivePipEntry> m_entries;
        uint64_t                          m_nextID   = 1;
        uint64_t                          m_revision = 0;
    };
}
