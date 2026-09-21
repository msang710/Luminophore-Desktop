#pragma once
#include "LuminophoreSpatialIndependent.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <set>
#include <vector>

struct SLuminophoreSpatialCommand;
struct SLuminophoreSpatialTransactionResult;
enum class eLuminophoreWindowPlacementMode : uint8_t;

enum class eLuminophoreSpatialDirection : uint8_t {
    UP,
    RIGHT,
    DOWN,
    LEFT,
};

eLuminophoreSpatialDirection luminophoreSpatialDirectionForDelta(int64_t dx, int64_t dy);

struct SLuminophoreBoardPoint {
    int64_t x = 0;
    int64_t y = 0;

    bool    operator==(const SLuminophoreBoardPoint&) const  = default;
    auto    operator<=>(const SLuminophoreBoardPoint&) const = default;
};

struct SLuminophoreBoardExtent {
    int                     columns = 1;
    int                     rows    = 1;

    static SLuminophoreBoardExtent fromPhysicalExtent(int width, int height);
    bool                    contains(const SLuminophoreBoardPoint& point) const;
    bool                    operator==(const SLuminophoreBoardExtent&) const = default;
};

struct SLuminophoreViewRect {
    SLuminophoreBoardPoint origin  = {};
    int             columns = 1;
    int             rows    = 1;

    bool            contains(const SLuminophoreBoardPoint& point) const;
    bool            operator==(const SLuminophoreViewRect&) const = default;
};

using LuminophoreWindowKey = uint64_t;

enum class eLuminophorePresentationMode : uint8_t {
    NORMAL,
    WIDE,
    DESKTOP,
};

struct SLuminophoreOutputView {
    uint64_t                     outputID  = 0;
    SLuminophoreViewRect                rect      = {};
    std::optional<LuminophoreWindowKey> anchorKey = std::nullopt;

    uint64_t                     boardID                                  = 0;
    bool                         operator==(const SLuminophoreOutputView&) const = default;
};

struct SLuminophoreNormalizedBox {
    static constexpr int BASIS = 10000;

    int                  x      = 1000;
    int                  y      = 1000;
    int                  width  = 8000;
    int                  height = 8000;
    // Client dimensions are logical pixels, independent of the host/view size.
    // Zero retains the legacy proportional representation for old fixtures.
    int  logicalWidth  = 0;
    int  logicalHeight = 0;

    bool valid() const;
    bool operator==(const SLuminophoreNormalizedBox&) const = default;
};

struct SLuminophoreTiledPlacement {
    LuminophoreWindowKey   key   = 0;
    SLuminophoreBoardPoint point = {};

    bool            operator==(const SLuminophoreTiledPlacement&) const = default;
};

struct SLuminophoreFloatingPlacement {
    LuminophoreWindowKey      key      = 0;
    SLuminophoreBoardPoint    host     = {};
    SLuminophoreNormalizedBox localBox = {};

    bool               operator==(const SLuminophoreFloatingPlacement&) const = default;
};

struct SLuminophoreSpatialSnapshot {
    SLuminophoreBoardExtent                     extent                 = {};
    SLuminophoreViewRect                        view                   = {};
    std::vector<SLuminophoreTiledPlacement>     tiled                  = {};
    std::vector<SLuminophoreFloatingPlacement>  floating               = {};
    eLuminophorePresentationMode                presentationMode       = eLuminophorePresentationMode::NORMAL;
    std::vector<SLuminophoreOutputView>         outputViews            = {};
    std::optional<LuminophoreWindowKey>         wideKey                = std::nullopt;
    uint64_t                             outputTopologyRevision = 0;
    uint64_t                             revision               = 0;

    eLuminophorePresentationMode                desktopReturnMode = eLuminophorePresentationMode::NORMAL;

    std::optional<SLuminophoreIndependentState> independent;
    bool                                 operator==(const SLuminophoreSpatialSnapshot&) const = default;
};

enum class eLuminophoreSpatialMoveResult : uint8_t {
    MOVED,
    PUSHED,
    RELOCATED,
    SWAPPED,
    REJECTED,
};

class CLuminophoreSpatialModel {
  public:
    CLuminophoreSpatialModel();
    // Baseline regression fixtures opt in explicitly; runtime construction is independent.
    static CLuminophoreSpatialModel            finiteFixture(SLuminophoreBoardExtent extent);
    bool                                independentBoards() const;
    bool                                isWideKey(LuminophoreWindowKey key) const;
    std::optional<uint64_t>             boardFor(LuminophoreWindowKey key) const;
    std::optional<uint64_t>             outputFor(LuminophoreWindowKey key) const;

    const SLuminophoreBoardExtent&             extent() const;
    const SLuminophoreViewRect&                view() const;
    const std::vector<SLuminophoreOutputView>& outputViews() const;
    uint64_t                            outputTopologyRevision() const;
    uint64_t                            revision() const;
    SLuminophoreSpatialSnapshot                snapshot() const;
    SLuminophoreSpatialTransactionResult       transact(const SLuminophoreSpatialCommand& command);
    SLuminophoreSpatialTransactionResult       preview(const SLuminophoreSpatialCommand& command) const;
    bool                                validate() const;
    bool                                restoreSpatial(const SLuminophoreSpatialSnapshot& saved, const std::set<LuminophoreWindowKey>& retained, const std::set<LuminophoreWindowKey>& excluded = {});

    bool                                addTiled(LuminophoreWindowKey key, std::optional<SLuminophoreBoardPoint> preferred = std::nullopt);
    bool                                remove(LuminophoreWindowKey key);
    std::optional<SLuminophoreBoardPoint>      coordinateOf(LuminophoreWindowKey key) const;
    std::optional<LuminophoreWindowKey>        tiledAt(const SLuminophoreBoardPoint& point) const;
    std::vector<LuminophoreWindowKey>          tiledWindows() const;
    std::vector<LuminophoreWindowKey>          visibleTiled() const;
    eLuminophoreSpatialMoveResult              moveTiled(LuminophoreWindowKey key, eLuminophoreSpatialDirection direction);

    bool                                attachFloating(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox = {});
    bool                                updateFloating(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox);
    bool                                detachFloating(LuminophoreWindowKey key);
    std::optional<SLuminophoreBoardPoint>      floatingHostOf(LuminophoreWindowKey key) const;
    std::vector<LuminophoreWindowKey>          floatingAt(const SLuminophoreBoardPoint& host) const;

    bool                                moveView(eLuminophoreSpatialDirection direction, uint64_t outputID = 0, uint64_t expectedTopologyRevision = 0);
    bool                                adjustView(eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor, uint64_t outputID = 0, uint64_t expectedTopologyRevision = 0);
    bool                                configureOutputViews(std::vector<SLuminophoreOutputView> views, uint64_t topologyRevision);
    bool                                enterWide(LuminophoreWindowKey key);
    bool                                exitWide();
    bool                                toggleWide(LuminophoreWindowKey key);
    bool                                resetDefaultView(const SLuminophoreBoardPoint& anchor = {});
    bool                                reconfigureExtent(SLuminophoreBoardExtent extent);

  private:
    explicit CLuminophoreSpatialModel(SLuminophoreBoardExtent extent);
    SLuminophoreSpatialTransactionResult        transactIndependent(const SLuminophoreSpatialCommand& command);
    void                                 syncIndependentCaches();
    std::optional<Luminophore::Spatial::SState> occupancyState(bool repair = false) const;
    void                                 assignOccupancy(Luminophore::Spatial::SState state);
    std::optional<SLuminophoreIndependentState> m_independent;
    using TiledByPoint = std::map<SLuminophoreBoardPoint, LuminophoreWindowKey>;

    SLuminophoreBoardPoint                adjacent(const SLuminophoreBoardPoint& point, eLuminophoreSpatialDirection direction) const;
    std::optional<SLuminophoreBoardPoint> firstVacant() const;
    std::optional<SLuminophoreBoardPoint> relocationFor(const SLuminophoreBoardPoint& occupied, const SLuminophoreBoardPoint& source) const;
    bool                           pushChain(TiledByPoint& state, const SLuminophoreBoardPoint& point, eLuminophoreSpatialDirection direction) const;
    bool                           addTiledImpl(LuminophoreWindowKey key, std::optional<SLuminophoreBoardPoint> preferred);
    bool                           removeImpl(LuminophoreWindowKey key);
    eLuminophoreSpatialMoveResult         moveTiledImpl(LuminophoreWindowKey key, eLuminophoreSpatialDirection direction);
    eLuminophoreSpatialMoveResult         moveTiledToImpl(LuminophoreWindowKey key, const SLuminophoreBoardPoint& target, eLuminophoreSpatialDirection direction);
    bool                           attachFloatingImpl(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox);
    bool                           updateFloatingImpl(LuminophoreWindowKey key, const SLuminophoreBoardPoint& host, const SLuminophoreNormalizedBox& localBox);
    bool                           detachFloatingImpl(LuminophoreWindowKey key);
    bool                           moveViewImpl(eLuminophoreSpatialDirection direction);
    bool                           adjustViewImpl(eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor);
    bool                           moveOutputViewImpl(uint64_t outputID, uint64_t expectedTopologyRevision, eLuminophoreSpatialDirection direction);
    bool                           adjustOutputViewImpl(uint64_t outputID, uint64_t expectedTopologyRevision, eLuminophoreSpatialDirection direction, const SLuminophoreBoardPoint& anchor);
    bool                           configureOutputViewsImpl(std::vector<SLuminophoreOutputView> views, uint64_t topologyRevision);
    bool                           configureTopologyImpl(SLuminophoreBoardExtent extent, std::vector<uint64_t> outputIDs, uint64_t topologyRevision);
    bool                           enterWideImpl(LuminophoreWindowKey key);
    bool                           exitWideImpl();
    bool                           toggleDesktopImpl();
    bool                           visibleInNormal(LuminophoreWindowKey key) const;
    void                           normalizeWideState();
    std::optional<uint64_t>        outputContaining(const SLuminophoreBoardPoint& point) const;
    bool                           visibleOnOutput(LuminophoreWindowKey key, uint64_t outputID) const;
    void                           setOutputAnchor(uint64_t outputID, std::optional<LuminophoreWindowKey> key);
    void                           normalizeOutputAnchors();
    bool                           resetDefaultViewImpl(const SLuminophoreBoardPoint& anchor);
    bool                           reconfigureExtentImpl(SLuminophoreBoardExtent extent);
    bool             observeWindowImpl(LuminophoreWindowKey key, eLuminophoreWindowPlacementMode mode, std::optional<SLuminophoreBoardPoint> preferred, std::optional<SLuminophoreNormalizedBox> localBox);
    void             eraseFloating(LuminophoreWindowKey key);
    void             clampView();
    void             commitTiled(TiledByPoint&& next);

    SLuminophoreBoardExtent m_extent;
    SLuminophoreViewRect    m_view;
    std::vector<SLuminophoreOutputView>                          m_outputViews;
    uint64_t                                              m_outputTopologyRevision = 0;
    eLuminophorePresentationMode                                 m_presentationMode       = eLuminophorePresentationMode::NORMAL;
    std::optional<LuminophoreWindowKey>                          m_wideKey                = std::nullopt;
    TiledByPoint                                          m_tiled;
    std::map<LuminophoreWindowKey, SLuminophoreBoardPoint>              m_tiledCoordinates;
    std::map<LuminophoreWindowKey, SLuminophoreBoardPoint>              m_floatingHosts;
    std::map<LuminophoreWindowKey, SLuminophoreNormalizedBox>           m_floatingBoxes;
    std::map<SLuminophoreBoardPoint, std::vector<LuminophoreWindowKey>> m_floatingByPoint;
    eLuminophorePresentationMode                                 m_desktopReturnMode = eLuminophorePresentationMode::NORMAL;
    uint64_t                                              m_revision          = 0;
};
