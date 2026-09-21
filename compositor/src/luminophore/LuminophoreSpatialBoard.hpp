#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <optional>
#include <variant>
#include <vector>

// Pure candidate state. Not connected to the native runtime until T06/T07.
namespace Luminophore::Spatial {
    using BoardID   = uint64_t;
    using WindowKey = uint64_t;
    struct SPoint {
        int64_t x                                = 0;
        int64_t y                                = 0;
        auto    operator<=>(const SPoint&) const = default;
    };
    struct SRect {
        SPoint  origin  = {};
        int64_t columns = 1;
        int64_t rows    = 1;
        bool    valid() const;
        bool    contains(SPoint point) const;
        bool    operator==(const SRect&) const = default;
    };
    enum class eViewOrigin {
        USER,
        AUTO,
    };
    enum class eRemovalCause {
        CLOSE,
        MINIMIZE,
        TRANSFER,
        RECLASSIFY,
    };
    enum class eStatus {
        APPLIED,
        NO_CHANGE,
        INVALID,
        STALE,
        OVERFLOW,
    };
    struct SBoard {
        SRect                       view                            = {};
        int64_t                     defaultColumns                  = 1;
        int64_t                     defaultRows                     = 1;
        eViewOrigin                 lastChange                      = eViewOrigin::USER;
        std::optional<SPoint>       lastAnchor                      = {};
        std::map<SPoint, WindowKey> tiled                           = {};
        bool                        operator==(const SBoard&) const = default;
    };
    struct SState {
        std::map<BoardID, SBoard> boards                          = {};
        std::optional<WindowKey>  focus                           = {};
        uint64_t                  revision                        = 0;
        bool                      operator==(const SState&) const = default;
    };
    struct SCreate {
        BoardID               board = 0;
        WindowKey             key   = 0;
        std::optional<SPoint> reference;
        std::string           initialPlacement;
    };
    struct SRemove {
        WindowKey     key   = 0;
        eRemovalCause cause = eRemovalCause::CLOSE;
    };
    struct SMove {
        WindowKey key    = 0;
        BoardID   board  = 0;
        SPoint    target = {};
    };
    struct SSetView {
        BoardID board = 0;
        SRect   view  = {};
    };
    struct SSetFocus {
        std::optional<WindowKey> key = {};
    };
    using Command = std::variant<SCreate, SRemove, SMove, SSetView, SSetFocus>;
    struct SResult {
        eStatus status = eStatus::INVALID;
        SState  state  = {};
    };
    std::optional<int64_t> checkedAdd(int64_t a, int64_t b);
    bool                   validate(const SState& state);
    SResult                transact(const SState& state, uint64_t expectedRevision, const Command& command);
}
