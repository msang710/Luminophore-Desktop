#pragma once

#include "LuminophoreSpatialModel.hpp"
#include "LuminophoreSpatialResize.hpp"

#include <optional>
#include <variant>

struct SAddTiledCommand {
    LuminophoreWindowKey                  key       = 0;
    std::optional<SLuminophoreBoardPoint> preferred = std::nullopt;
};

struct SRemoveWindowCommand {
    LuminophoreWindowKey key = 0;
};

struct SMoveTiledCommand {
    LuminophoreWindowKey         key       = 0;
    eLuminophoreSpatialDirection direction = eLuminophoreSpatialDirection::UP;
};

struct SMoveWindowToCommand {
    LuminophoreWindowKey   key                      = 0;
    SLuminophoreBoardPoint point                    = {};
    uint64_t               outputID                 = 0;
    uint64_t               expectedTopologyRevision = 0;
};

struct SMoveOutputViewToCommand {
    uint64_t               outputID                 = 0;
    SLuminophoreBoardPoint origin                   = {};
    uint64_t               expectedTopologyRevision = 0;
};

struct SResizeOutputViewCommand {
    uint64_t             outputID                 = 0;
    SLuminophoreViewRect rect                     = {};
    uint64_t             expectedTopologyRevision = 0;
};

struct SAttachFloatingCommand {
    LuminophoreWindowKey      key      = 0;
    SLuminophoreBoardPoint    host     = {};
    SLuminophoreNormalizedBox localBox = {};
    uint64_t                  outputID = 0;
};

struct SUpdateFloatingCommand {
    LuminophoreWindowKey      key      = 0;
    SLuminophoreBoardPoint    host     = {};
    SLuminophoreNormalizedBox localBox = {};
    uint64_t                  outputID = 0;
};

struct SDetachFloatingCommand {
    LuminophoreWindowKey key = 0;
};

struct SFocusDirectionCommand {
    LuminophoreWindowKey         key                      = 0;
    eLuminophoreSpatialDirection direction                = eLuminophoreSpatialDirection::UP;
    uint64_t                     outputID                 = 0;
    uint64_t                     expectedTopologyRevision = 0;
};

struct SMoveViewCommand {
    eLuminophoreSpatialDirection        direction                = eLuminophoreSpatialDirection::UP;
    std::optional<LuminophoreWindowKey> focusedKey               = std::nullopt;
    uint64_t                            outputID                 = 0;
    uint64_t                            expectedTopologyRevision = 0;
};

struct SAdjustViewCommand {
    eLuminophoreSpatialDirection        direction                = eLuminophoreSpatialDirection::UP;
    SLuminophoreBoardPoint              anchor                   = {};
    std::optional<LuminophoreWindowKey> anchorKey                = std::nullopt;
    uint64_t                            outputID                 = 0;
    uint64_t                            expectedTopologyRevision = 0;
};

struct SConfigureOutputViewsCommand {
    std::vector<SLuminophoreOutputView> views            = {};
    uint64_t                            topologyRevision = 0;
};

struct SConfigureTopologyCommand {
    SLuminophoreBoardExtent extent           = {};
    std::vector<uint64_t>   outputIDs        = {};
    uint64_t                topologyRevision = 0;
};

struct SEnterWideCommand {
    LuminophoreWindowKey key = 0;
};

struct SExitWideCommand {};

struct SToggleDesktopCommand {};

struct SToggleWideCommand {
    LuminophoreWindowKey key = 0;
};

struct SResetViewCommand {
    SLuminophoreBoardPoint anchor = {};
};

struct SReconfigureExtentCommand {
    SLuminophoreBoardExtent extent = {};
};

enum class eLuminophoreWindowPlacementMode : uint8_t {
    ABSENT,
    TILED,
    FLOATING,
};

struct SSpatialFocusCommand {
    std::optional<LuminophoreWindowKey>                          key;
    uint64_t                                                     outputID = 0;
    std::map<LuminophoreWindowKey, Luminophore::Spatial::SPoint> clientMinimums;
    bool                                                         reveal = false;
};
struct SSpatialTopologyCommand {
    std::vector<SLuminophoreOutputGeometry> outputs;
    uint64_t                                revision = 0;
    int                                     columns = 2, rows = 2;
};
struct SSpatialResizeCommand {
    LuminophoreWindowKey                                         key     = 0;
    int                                                          side    = 1;
    int64_t                                                      delta   = 0;
    uint64_t                                                     face    = 0;
    int64_t                                                      minimum = 100;
    std::optional<Luminophore::Spatial::SResizeRequest>          second;
    std::map<LuminophoreWindowKey, Luminophore::Spatial::SPoint> clientMinimums;
};
struct SObserveWindowCommand {
    LuminophoreWindowKey                     key          = 0;
    eLuminophoreWindowPlacementMode          mode         = eLuminophoreWindowPlacementMode::ABSENT;
    std::optional<SLuminophoreBoardPoint>    preferred    = std::nullopt;
    std::optional<SLuminophoreNormalizedBox> localBox     = std::nullopt;
    uint64_t                                 outputID     = 0;
    bool                                     closed       = false;
    LuminophoreWindowKey                     causalSource = 0;
    std::string                              initialPlacement;
    bool                                     directLaunch = false;
};

using LuminophoreSpatialPayload =
    std::variant<SAddTiledCommand, SRemoveWindowCommand, SMoveTiledCommand, SAttachFloatingCommand, SUpdateFloatingCommand, SDetachFloatingCommand, SMoveViewCommand,
                 SFocusDirectionCommand, SAdjustViewCommand, SConfigureOutputViewsCommand, SConfigureTopologyCommand, SEnterWideCommand, SMoveWindowToCommand,
                 SMoveOutputViewToCommand, SResizeOutputViewCommand, SExitWideCommand, SToggleWideCommand, SToggleDesktopCommand, SResetViewCommand, SReconfigureExtentCommand,
                 SObserveWindowCommand, SSpatialFocusCommand, SSpatialTopologyCommand, SSpatialResizeCommand>;

struct SLuminophoreSpatialCommand {
    uint64_t                  expectedRevision = 0;
    LuminophoreSpatialPayload payload          = SRemoveWindowCommand{};
};

enum class eLuminophoreSpatialTransactionStatus : uint8_t {
    APPLIED,
    NO_CHANGE,
    STALE_REVISION,
    STALE_TOPOLOGY,
    INVALID_COMMAND,
    NO_CAPACITY,
    COMMIT_FAILED,
};

struct SLuminophoreSpatialTransactionResult {
    eLuminophoreSpatialTransactionStatus status         = eLuminophoreSpatialTransactionStatus::INVALID_COMMAND;
    eLuminophoreSpatialMoveResult        moveResult     = eLuminophoreSpatialMoveResult::REJECTED;
    bool                                 updatesFocus   = false;
    std::optional<LuminophoreWindowKey>  nextFocusedKey = std::nullopt;
    SLuminophoreSpatialSnapshot          snapshot       = {};
};
