#pragma once

#include <expected>
#include <string>
#include <optional>

#include "../../../desktop/DesktopTypes.hpp"
#include "../../../desktop/Workspace.hpp"
#include "../../../helpers/math/Direction.hpp"
#include "../ConfigErrors.hpp"

namespace Fullscreen {
    enum eFullscreenMode : int8_t;
}

namespace Luminophore {
    enum eOccupyOutputAction : uint8_t;
    enum eShellProjectionPhase : uint8_t;
    enum eShellProjectionPlane : uint8_t;
    enum eShellWidgetRole : uint8_t;
    struct SShellProjectionStyle;
}

namespace Config::Actions {
    struct SActionResult {
        bool passEvent = false;
    };

    using eActionErrorLevel = Config::eConfigErrorLevel;
    using eActionErrorCode  = Config::eConfigErrorCode;
    using SActionError      = Config::SConfigError;
    using Config::toString;

    inline std::unexpected<SActionError> actionError(std::string message, eActionErrorLevel level = eActionErrorLevel::ERROR, eActionErrorCode code = eActionErrorCode::UNKNOWN) {
        return Config::configError(std::move(message), level, code);
    }

    enum eTogglableAction : uint8_t {
        TOGGLE_ACTION_TOGGLE = 0,
        TOGGLE_ACTION_ENABLE,
        TOGGLE_ACTION_DISABLE,
    };

    using ActionResult = std::expected<SActionResult, SActionError>;

    ActionResult closeWindow(std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult killWindow(std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult signalWindow(int sig, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult floatWindow(eTogglableAction action, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult pseudoWindow(eTogglableAction action, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult pinWindow(eTogglableAction action, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult fullscreenWindow(Fullscreen::eFullscreenMode mode, bool layoutAware, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult fullscreenWindow(Fullscreen::eFullscreenMode internalMode, Fullscreen::eFullscreenMode clientMode, bool layoutAware,
                                  std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult occupyOutput(Luminophore::eOccupyOutputAction action, const std::string& serial = {}, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult minimizeWindow(std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult restoreWindow(std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult shellProjection(const std::string& surfaceNamespace, const std::string& generation, uint64_t revision, uint64_t contentRevision,
                                 Luminophore::eShellProjectionPhase phase, Luminophore::eShellProjectionPlane requestedPlane, Luminophore::eShellWidgetRole role,
                                 const Luminophore::SShellProjectionStyle& style);
    ActionResult moveToWorkspace(PHLWORKSPACE ws, bool silent, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult moveFocus(Math::eDirection dir);
    ActionResult focus(PHLWINDOW window);
    ActionResult moveInDirection(Math::eDirection dir, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult swapInDirection(Math::eDirection dir, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult swapWith(PHLWINDOW other, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult focusCurrentOrLast();
    ActionResult focusUrgentOrLast();
    ActionResult center(std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult moveCursorToCorner(int corner, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult resize(const Vector2D& size, bool relative = false, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult move(const Vector2D& pos, bool relative = false, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult cycleNext(const bool next, std::optional<bool> onlyTiled, std::optional<bool> onlyFloating, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult tag(const std::string& tag, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult clearTags(std::optional<PHLWINDOW> w = std::nullopt);
    ActionResult pass(std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult pass(uint32_t modMask, uint32_t key, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult sendKeyState(uint32_t modMask, uint32_t key, uint32_t state, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult swapNext(const bool next, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult alterZOrder(const std::string& mode, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult setProp(const std::string& prop, const std::string& val, std::optional<PHLWINDOW> window = std::nullopt /* Active */);

    ActionResult changeWorkspace(PHLWORKSPACE ws);
    ActionResult changeWorkspace(const std::string& ws);

    ActionResult focusMonitor(PHLMONITOR mon);

    ActionResult moveCursor(const Vector2D& pos);
    ActionResult exit();
    ActionResult forceRendererReload();
    ActionResult toggleSwallow();
    ActionResult setSubmap(const std::string& submap);
    ActionResult dpms(eTogglableAction action, std::optional<PHLMONITOR> mon);
    ActionResult forceIdle(float seconds);
    ActionResult global(const std::string& action);
    ActionResult event(const std::string& data);

    ActionResult mouse(const std::string& action);

    ActionResult spatialMoveView(Math::eDirection direction);
    ActionResult spatialAdjustView(Math::eDirection direction, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult spatialFocusDirection(Math::eDirection direction, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult spatialMoveWindow(Math::eDirection direction, std::optional<PHLWINDOW> window = std::nullopt /* Active */);
    ActionResult spatialToggleDesktop();
    ActionResult spatialToggleWide(std::optional<PHLWINDOW> window = std::nullopt /* Active */);

    ActionResult releaseInputCapture();

    class CActionState {
      public:
        CActionState()  = default;
        ~CActionState() = default;

        int         m_passPressed   = -1; // -1 = dynamic (press+release), 0 = released, 1 = pressed
        uint32_t    m_lastCode      = 0;  // last keycode (keyboard event), 0 if last was mouse
        uint32_t    m_lastMouseCode = 0;  // last mouse button code, 0 if last was keyboard
        uint32_t    m_timeLastMs    = 0;  // timestamp of last key/mouse event
        std::string m_currentSubmap = ""; // current keybind submap name
    };

    UP<CActionState>& state();
};
