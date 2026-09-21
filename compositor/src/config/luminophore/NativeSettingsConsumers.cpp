#include "RuntimeSettingsAdapter.hpp"
#include "SettingsService.hpp"
#include "DesktopSettings.hpp"
#include "InputRuntime.hpp"
#include "MotionSettings.hpp"
#include "GeneralSettings.hpp"
#include "../shared/complex/ComplexDataTypes.hpp"
#include "../shared/animation/AnimationTree.hpp"
#include "../../animation/AnimationManager.hpp"
#include "../../luminophore/LuminophoreVisualSettings.hpp"
#include "../../luminophore/LuminophoreShellProjection.hpp"
#include "../ConfigManager.hpp"
#include "../supplementary/propRefresher/PropRefresher.hpp"
#include <stdexcept>
#include "../../luminophore/LuminophoreDisplayIdleController.hpp"
#include "../../state/MonitorState.hpp"
#include "../../state/MonitorLayoutController.hpp"
#include "../../desktop/state/WindowState.hpp"
#include "../../desktop/view/Window.hpp"
#include "../../render/Renderer.hpp"
#include "../../protocols/PrimarySelection.hpp"
#include "../../protocols/ColorManagement.hpp"
#include "../../managers/SeatManager.hpp"

using namespace Luminophore::Settings;

// Resolve on each operation; never retain loader-owned pointers across reload.
// This bridge is removed when ConfigValue consumers use owned storage directly.
static Config::SConfigOptionReply resolve(const std::string& name, const std::type_info& expected) {
    if (!Config::mgr())
        throw std::runtime_error("config manager unavailable");
    auto value = Config::mgr()->getConfigValue(name);
    if (!value.dataptr || !*value.dataptr || !value.type || *value.type != expected)
        throw std::runtime_error("unsupported native setting type: " + name);
    return value;
}

template <typename T, typename V>
static SRuntimeSlot slot(const std::string& name) {
    return {[name]() -> Value { return static_cast<V>(*static_cast<T*>(*resolve(name, typeid(T)).dataptr)); },
            [name](const Value& value) { *static_cast<T*>(*resolve(name, typeid(T)).dataptr) = static_cast<T>(std::get<V>(value)); }, [name] { resolve(name, typeid(T)); }};
}

static SRuntimeSlot vectorSlot(const std::string& name, bool y) {
    return {[name, y]() -> Value {
                const auto v = static_cast<Config::VEC2*>(*resolve(name, typeid(Config::VEC2)).dataptr);
                return y ? v->y : v->x;
            },
            [name, y](const Value& value) {
                auto v            = static_cast<Config::VEC2*>(*resolve(name, typeid(Config::VEC2)).dataptr);
                (y ? v->y : v->x) = static_cast<Config::FLOAT>(std::get<double>(value));
            },
            [name] { resolve(name, typeid(Config::VEC2)); }};
}

static SRuntimeSlot gapSlot(const std::string& name) {
    return {[name]() -> Value {
                const auto* gap = static_cast<Config::CCssGapData*>(*resolve(name, typeid(Config::CCssGapData)).dataptr);
                if (gap->m_top != gap->m_right || gap->m_top != gap->m_bottom || gap->m_top != gap->m_left)
                    throw std::runtime_error("nonuniform gaps require explicit migration");
                return int64_t(gap->m_top);
            },
            [name](const Value& value) { *static_cast<Config::CCssGapData*>(*resolve(name, typeid(Config::CCssGapData)).dataptr) = Config::CCssGapData(std::get<int64_t>(value)); },
            [name] { resolve(name, typeid(Config::CCssGapData)); }};
}

static SRuntimeSlot emptyStringSlot(const std::string& name) {
    return {[name]() -> Value {
                const auto& text = *static_cast<std::string*>(*resolve(name, typeid(std::string)).dataptr);
                return text == STRVAL_EMPTY ? std::string{} : text;
            },
            [name](const Value& value) {
                const auto& text                                                        = std::get<std::string>(value);
                *static_cast<std::string*>(*resolve(name, typeid(std::string)).dataptr) = text.empty() ? STRVAL_EMPTY : text;
            },
            [name] { resolve(name, typeid(std::string)); }};
}

static SRuntimeSlot colorSlot(const std::string& name) {
    return {[name]() -> Value { return generalColor(*static_cast<Config::INTEGER*>(*resolve(name, typeid(Config::INTEGER)).dataptr)); },
            [name](const Value& value) { *static_cast<Config::INTEGER*>(*resolve(name, typeid(Config::INTEGER)).dataptr) = parseGeneralColor(std::get<std::string>(value)); },
            [name] { resolve(name, typeid(Config::INTEGER)); }};
}

static SRuntimeSlot gradientSlot(const std::string& name) {
    const bool inactive = name == "decoration:shadow:color_inactive";
    return {[name, inactive]() -> Value {
                const auto reply = resolve(name, typeid(Config::CGradientValueData));
                if (inactive && !reply.setByUser)
                    return std::string{"inherit"};
                return shadowGradient(*static_cast<Config::CGradientValueData*>(*reply.dataptr));
            },
            [name, inactive](const Value& value) {
                const auto& text                                                                                      = std::get<std::string>(value);
                *static_cast<Config::CGradientValueData*>(*resolve(name, typeid(Config::CGradientValueData)).dataptr) = parseShadowGradient(text);
                if (inactive && Config::mgr()->type() == Config::CONFIG_LUMINOPHORE)
                    *static_cast<bool*>(*resolve("luminophore:shadow_inactive_inherit", typeid(bool)).dataptr) = text == "inherit";
            },
            [name, inactive] {
                resolve(name, typeid(Config::CGradientValueData));
                if (inactive && Config::mgr()->type() == Config::CONFIG_LUMINOPHORE)
                    resolve("luminophore:shadow_inactive_inherit", typeid(bool));
            }};
}

static SRuntimeSlot edgeGapSlot(const std::string& name, int64_t Config::CCssGapData::* edge) {
    return {[name, edge]() -> Value { return (*static_cast<Config::CCssGapData*>(*resolve(name, typeid(Config::CCssGapData)).dataptr)).*edge; },
            [name, edge](const Value& value) { (*static_cast<Config::CCssGapData*>(*resolve(name, typeid(Config::CCssGapData)).dataptr)).*edge = std::get<int64_t>(value); },
            [name] { resolve(name, typeid(Config::CCssGapData)); }};
}

static std::string ownedCurve(const std::string& curve) {
    if (curve == "default")
        return curve;
    if (curve == "spring:easy")
        return "spring:luminophore-settings-easy";
    return "luminophore-settings-" + curve;
}

static void motionWritable(const Snapshot& values) {
    if (!Animation::mgr() || !Config::animationTree() || !Luminophore::shellProjection() || !Config::Supplementary::refresher())
        throw std::runtime_error("motion consumers unavailable");
    for (const auto& node : motionNodes(values)) {
        const auto config = Config::animationTree()->getAnimationPropertyConfig(node.name);
        if (!config || !config->pValues.lock())
            throw std::runtime_error("missing animation destination: " + node.name);
    }
}

static void verifyMotion(const Snapshot& values) {
    if (!Config::animationTree())
        throw std::runtime_error("animation tree unavailable");
    for (const auto& node : motionNodes(values)) {
        const auto config = Config::animationTree()->getAnimationPropertyConfig(node.name);
        if (!config)
            throw std::runtime_error("missing animation node");
        const auto effective = config->pValues.lock();
        if (!effective || effective->internalEnabled != node.enabled || effective->internalSpeed != node.speed ||
            (effective->internalBezier != node.curve && effective->internalBezier != ownedCurve(node.curve)) || effective->internalStyle != node.style)
            throw std::runtime_error("animation settings drift: " + node.name);
    }
}

static Luminophore::SVisualSettings visual(const Snapshot& v) {
    return {static_cast<uint32_t>(std::get<int64_t>(v.at("visual.schema_version"))), std::get<std::string>(v.at("visual.preset")), std::get<bool>(v.at("visual.enabled")),
            std::get<bool>(v.at("visual.breathing")), std::get<double>(v.at("visual.intensity"))};
}

std::shared_ptr<CRuntimeSettingsAdapter> Luminophore::Settings::makeRuntimeAdapter(const Snapshot& initial, bool initialize) {
    auto                                pending       = std::make_shared<Snapshot>(initial);
    auto                                motionCurrent = std::make_shared<Snapshot>(initial);
    std::map<std::string, SRuntimeSlot> slots         = {
        {"compositor.swallow_enabled", slot<Config::BOOL, bool>("misc:enable_swallow")},
        {"compositor.swallow_regex", emptyStringSlot("misc:swallow_regex")},
        {"compositor.swallow_exception_regex", emptyStringSlot("misc:swallow_exception_regex")},
        {"compositor.screen_shader", emptyStringSlot("decoration:screen_shader")},
        {"compositor.motion_blur_enabled", slot<Config::BOOL, bool>("decoration:motion_blur:enabled")},
        {"compositor.motion_blur_samples", slot<Config::INTEGER, int64_t>("decoration:motion_blur:samples")},
        {"compositor.snap_enabled", slot<Config::BOOL, bool>("general:snap:enabled")},
        {"compositor.snap_window_gap", slot<Config::INTEGER, int64_t>("general:snap:window_gap")},
        {"compositor.snap_monitor_gap", slot<Config::INTEGER, int64_t>("general:snap:monitor_gap")},
        {"compositor.snap_border_overlap", slot<Config::BOOL, bool>("general:snap:border_overlap")},
        {"compositor.snap_respect_gaps", slot<Config::BOOL, bool>("general:snap:respect_gaps")},
        {"compositor.cursor_start_output", emptyStringSlot("cursor:default_monitor")},
        {"compositor.zoom_factor", slot<Config::FLOAT, double>("cursor:zoom_factor")},
        {"compositor.locale", slot<std::string, std::string>("general:locale")},
        {"compositor.font_family", slot<std::string, std::string>("misc:font_family")},
        {"compositor.wake_on_key", slot<Config::BOOL, bool>("misc:key_press_enables_dpms")},
        {"compositor.wake_on_pointer", slot<Config::BOOL, bool>("misc:mouse_move_enables_dpms")},
        {"compositor.display_idle_minutes", slot<Config::INTEGER, int64_t>("misc:luminophore_monitor_idle_minutes")},
        {"compositor.primary_selection", slot<Config::BOOL, bool>("misc:middle_click_paste")},
        {"compositor.auto_hdr", slot<Config::INTEGER, int64_t>("render:cm_auto_hdr")},
        {"compositor.sdr_transfer", slot<std::string, std::string>("render:cm_sdr_eotf")},
        {"compositor.icc_vcgt", slot<Config::BOOL, bool>("render:icc_vcgt_enabled")},
        {"compositor.xwayland_native_pixels", slot<Config::BOOL, bool>("xwayland:force_zero_scaling")},
        {"compositor.background_color", colorSlot("misc:background_color")},
        {"compositor.shadow_enabled", slot<Config::BOOL, bool>("decoration:shadow:enabled")},
        {"compositor.shadow_range", slot<Config::INTEGER, int64_t>("decoration:shadow:range")},
        {"compositor.shadow_power", slot<Config::INTEGER, int64_t>("decoration:shadow:render_power")},
        {"compositor.shadow_sharp", slot<Config::BOOL, bool>("decoration:shadow:sharp")},
        {"compositor.shadow_color", gradientSlot("decoration:shadow:color")},
        {"compositor.shadow_inactive_color", gradientSlot("decoration:shadow:color_inactive")},
        {"compositor.shadow_scale", slot<Config::FLOAT, double>("decoration:shadow:scale")},
        {"compositor.shadow_offset_x", vectorSlot("decoration:shadow:offset", false)},
        {"compositor.shadow_offset_y", vectorSlot("decoration:shadow:offset", true)},
        {"compositor.float_gap_top", edgeGapSlot("general:float_gaps", &Config::CCssGapData::m_top)},
        {"compositor.float_gap_right", edgeGapSlot("general:float_gaps", &Config::CCssGapData::m_right)},
        {"compositor.float_gap_bottom", edgeGapSlot("general:float_gaps", &Config::CCssGapData::m_bottom)},
        {"compositor.float_gap_left", edgeGapSlot("general:float_gaps", &Config::CCssGapData::m_left)},
        {"compositor.xwayland_nearest_neighbor", slot<Config::BOOL, bool>("xwayland:use_nearest_neighbor")},
        {"compositor.allow_tearing", slot<Config::BOOL, bool>("general:allow_tearing")},
        {"compositor.pointer_focus_output", slot<Config::BOOL, bool>("misc:mouse_move_focuses_monitor")},
        {"compositor.fullscreen_focus_policy", slot<Config::INTEGER, int64_t>("misc:on_focus_under_fullscreen")},
        {"compositor.fullscreen_after_close", slot<Config::BOOL, bool>("misc:exit_window_retains_fullscreen")},
        {"compositor.zoom_rigid", slot<Config::BOOL, bool>("cursor:zoom_rigid")},
        {"compositor.zoom_detached_camera", slot<Config::BOOL, bool>("cursor:zoom_detached_camera")},
        {"compositor.zoom_disable_aa", slot<Config::BOOL, bool>("cursor:zoom_disable_aa")},
        {"compositor.fullscreen_opacity", slot<Config::FLOAT, double>("decoration:fullscreen_opacity")},
        {"compositor.dim_inactive", slot<Config::BOOL, bool>("decoration:dim_inactive")},
        {"compositor.dim_modal", slot<Config::BOOL, bool>("decoration:dim_modal")},
        {"compositor.dim_strength", slot<Config::FLOAT, double>("decoration:dim_strength")},
        {"compositor.dim_around", slot<Config::FLOAT, double>("decoration:dim_around")},
        {"compositor.blur_popups", slot<Config::BOOL, bool>("decoration:blur:popups")},
        {"compositor.blur_input_methods", slot<Config::BOOL, bool>("decoration:blur:input_methods")},
        {"compositor.render_unfocused_fps", slot<Config::INTEGER, int64_t>("misc:render_unfocused_fps")},
        {"input.drag_threshold", slot<Config::INTEGER, int64_t>("binds:drag_threshold")},
        {"input.scroll_event_delay", slot<Config::INTEGER, int64_t>("binds:scroll_event_delay")},
        {"input.cursor_inactive_timeout", slot<Config::FLOAT, double>("cursor:inactive_timeout")},
        {"input.cursor_no_warps", slot<Config::BOOL, bool>("cursor:no_warps")},
        {"input.cursor_persistent_warps", slot<Config::BOOL, bool>("cursor:persistent_warps")},
        {"input.cursor_hide_on_key_press", slot<Config::BOOL, bool>("cursor:hide_on_key_press")},
        {"input.cursor_hide_on_touch", slot<Config::BOOL, bool>("cursor:hide_on_touch")},
        {"input.cursor_hide_on_tablet", slot<Config::BOOL, bool>("cursor:hide_on_tablet")},
        {"input.cursor_warp_back_after_non_mouse_input", slot<Config::BOOL, bool>("cursor:warp_back_after_non_mouse_input")},
        {"input.resize_on_border", slot<Config::BOOL, bool>("general:resize_on_border")},
        {"input.extend_border_grab_area", slot<Config::INTEGER, int64_t>("general:extend_border_grab_area")},
        {"input.resize_on_border_inner_area", slot<Config::INTEGER, int64_t>("general:resize_on_border_inner_area")},
        {"input.hover_icon_on_border", slot<Config::BOOL, bool>("general:hover_icon_on_border")},
        {"input.resize_corner", slot<Config::INTEGER, int64_t>("general:resize_corner")},
        {"input.close_gesture_timeout", slot<Config::INTEGER, int64_t>("gestures:close_max_timeout")},
        {"touchdevice.transform", slot<Config::INTEGER, int64_t>("input:touchdevice:transform")},
        {"touchdevice.output", slot<std::string, std::string>("input:touchdevice:output")},
        {"touchdevice.enabled", slot<Config::BOOL, bool>("input:touchdevice:enabled")},
        {"virtualkeyboard.share_states", slot<Config::INTEGER, int64_t>("input:virtualkeyboard:share_states")},
        {"virtualkeyboard.release_pressed_on_close", slot<Config::BOOL, bool>("input:virtualkeyboard:release_pressed_on_close")},
        {"tablet.transform", slot<Config::INTEGER, int64_t>("input:tablet:transform")},
        {"tablet.output", slot<std::string, std::string>("input:tablet:output")},
        {"tablet.absolute_region_position", slot<Config::BOOL, bool>("input:tablet:absolute_region_position")},
        {"tablet.relative_input", slot<Config::BOOL, bool>("input:tablet:relative_input")},
        {"tablet.left_handed", slot<Config::BOOL, bool>("input:tablet:left_handed")},
        {"tablet.region_position_x", vectorSlot("input:tablet:region_position", false)},
        {"tablet.region_position_y", vectorSlot("input:tablet:region_position", true)},
        {"tablet.region_size_x", vectorSlot("input:tablet:region_size", false)},
        {"tablet.region_size_y", vectorSlot("input:tablet:region_size", true)},
        {"tablet.active_area_size_x", vectorSlot("input:tablet:active_area_size", false)},
        {"tablet.active_area_size_y", vectorSlot("input:tablet:active_area_size", true)},
        {"tablet.active_area_position_x", vectorSlot("input:tablet:active_area_position", false)},
        {"tablet.active_area_position_y", vectorSlot("input:tablet:active_area_position", true)},
        {"tablettool.eraser_button_mode", slot<Config::INTEGER, int64_t>("input:tablettool:eraser_button_mode")},
        {"tablettool.eraser_button_override", slot<Config::INTEGER, int64_t>("input:tablettool:eraser_button_override")},
        {"tablettool.pressure_range_min", slot<Config::FLOAT, double>("input:tablettool:pressure_range_min")},
        {"tablettool.pressure_range_max", slot<Config::FLOAT, double>("input:tablettool:pressure_range_max")},
        {"touchpad.disable_while_typing", slot<Config::BOOL, bool>("input:touchpad:disable_while_typing")},
        {"touchpad.natural_scroll", slot<Config::BOOL, bool>("input:touchpad:natural_scroll")},
        {"touchpad.scroll_factor", slot<Config::FLOAT, double>("input:touchpad:scroll_factor")},
        {"touchpad.middle_button_emulation", slot<Config::BOOL, bool>("input:touchpad:middle_button_emulation")},
        {"touchpad.tap_button_map", slot<std::string, std::string>("input:touchpad:tap_button_map")},
        {"touchpad.clickfinger_behavior", slot<Config::BOOL, bool>("input:touchpad:clickfinger_behavior")},
        {"touchpad.tap_to_click", slot<Config::BOOL, bool>("input:touchpad:tap-to-click")},
        {"touchpad.drag_lock", slot<Config::INTEGER, int64_t>("input:touchpad:drag_lock")},
        {"touchpad.tap_and_drag", slot<Config::BOOL, bool>("input:touchpad:tap-and-drag")},
        {"touchpad.flip_x", slot<Config::BOOL, bool>("input:touchpad:flip_x")},
        {"touchpad.flip_y", slot<Config::BOOL, bool>("input:touchpad:flip_y")},
        {"touchpad.drag_3fg", slot<Config::INTEGER, int64_t>("input:touchpad:drag_3fg")},
        {"input.kb_layout", slot<std::string, std::string>("input:kb_layout")},
        {"input.kb_model", slot<std::string, std::string>("input:kb_model")},
        {"input.kb_variant", slot<std::string, std::string>("input:kb_variant")},
        {"input.kb_options", slot<std::string, std::string>("input:kb_options")},
        {"input.kb_rules", slot<std::string, std::string>("input:kb_rules")},
        {"input.scroll_method", slot<std::string, std::string>("input:scroll_method")},
        {"input.scroll_button", slot<Config::INTEGER, int64_t>("input:scroll_button")},
        {"input.scroll_button_lock", slot<Config::BOOL, bool>("input:scroll_button_lock")},
        {"input.rotation", slot<Config::INTEGER, int64_t>("input:rotation")},
        {"input.numlock_by_default", slot<Config::BOOL, bool>("input:numlock_by_default")},
        {"input.resolve_binds_by_sym", slot<Config::BOOL, bool>("input:resolve_binds_by_sym")},
        {"input.follow_mouse", slot<Config::INTEGER, int64_t>("input:follow_mouse")},
        {"input.follow_mouse_threshold", slot<Config::FLOAT, double>("input:follow_mouse_threshold")},
        {"input.mouse_refocus", slot<Config::BOOL, bool>("input:mouse_refocus")},
        {"input.follow_mouse_shrink", slot<Config::INTEGER, int64_t>("input:follow_mouse_shrink")},
        {"input.off_window_axis_events", slot<Config::INTEGER, int64_t>("input:off_window_axis_events")},
        {"input.emulate_discrete_scroll", slot<Config::INTEGER, int64_t>("input:emulate_discrete_scroll")},
        {"input.focus_on_close", slot<Config::INTEGER, int64_t>("input:focus_on_close")},
        {"input.float_switch_override_focus", slot<Config::INTEGER, int64_t>("input:float_switch_override_focus")},
        {"input.accel_profile", slot<std::string, std::string>("input:accel_profile")},
        {"input.scroll_points", slot<std::string, std::string>("input:scroll_points")},
        {"input.kb_file", slot<std::string, std::string>("input:kb_file")},
        {"input.kb_snapshot", slot<std::string, std::string>("input:kb_snapshot")},
        {"input.repeat_rate", slot<Config::INTEGER, int64_t>("input:repeat_rate")},
        {"input.repeat_delay", slot<Config::INTEGER, int64_t>("input:repeat_delay")},
        {"input.sensitivity", slot<Config::FLOAT, double>("input:sensitivity")},
        {"input.scroll_factor", slot<Config::FLOAT, double>("input:scroll_factor")},
        {"input.natural_scroll", slot<Config::BOOL, bool>("input:natural_scroll")},
        {"input.left_handed", slot<Config::BOOL, bool>("input:left_handed")},
        {"input.force_no_accel", slot<Config::BOOL, bool>("input:force_no_accel")},
        {"compositor.gaps_in", gapSlot("general:gaps_in")},
        {"compositor.gaps_out", gapSlot("general:gaps_out")},
        {"compositor.default_view_columns", slot<Config::INTEGER, int64_t>("misc:luminophore_default_view_columns")},
        {"compositor.default_view_rows", slot<Config::INTEGER, int64_t>("misc:luminophore_default_view_rows")},
        {"compositor.border_size", slot<Config::INTEGER, int64_t>("general:border_size")},
        {"compositor.rounding", slot<Config::INTEGER, int64_t>("decoration:rounding")},
        {"compositor.active_opacity", slot<Config::FLOAT, double>("decoration:active_opacity")},
        {"compositor.inactive_opacity", slot<Config::FLOAT, double>("decoration:inactive_opacity")},
        {"compositor.dim_special", slot<Config::FLOAT, double>("decoration:dim_special")},
        {"compositor.blur_enabled", slot<Config::BOOL, bool>("decoration:blur:enabled")},
        {"compositor.blur_size", slot<Config::INTEGER, int64_t>("decoration:blur:size")},
        {"compositor.blur_passes", slot<Config::INTEGER, int64_t>("decoration:blur:passes")},
        {"compositor.vrr", slot<Config::INTEGER, int64_t>("misc:vrr")},
    };
    // Desired next-session value is separate from the active protocol/shader state.
    if (Config::mgr()->type() == Config::CONFIG_LUMINOPHORE)
        slots["compositor.color_management"] = slot<Config::BOOL, bool>("luminophore:next_session_cm_enabled");
    else {
        const auto active    = *static_cast<Config::BOOL*>(*resolve("render:cm_enabled", typeid(Config::BOOL)).dataptr);
        const auto unchanged = [active](const Value& value) {
            if (std::get<bool>(value) != active)
                throw std::runtime_error("next-session color management requires native settings");
        };
        slots["compositor.color_management"] = {[active]() -> Value { return active; }, unchanged, {}, unchanged};
    }
    slots["compositor.lock_background"] = slot<Config::BOOL, bool>("misc:session_lock_xray");
    slots["compositor.lock_blur"]       = slot<Config::BOOL, bool>("misc:session_lock_blur");
    for (const auto& [key, value] : initial) {
        if (key.starts_with("motion."))
            slots[key] = {[key, motionCurrent]() -> Value {
                              verifyMotion(*motionCurrent);
                              return motionCurrent->at(key);
                          },
                          [key, pending](const Value& v) { pending->at(key) = v; }, [motionCurrent] { motionWritable(*motionCurrent); }};
        if (key.starts_with("visual."))
            slots[key] = {[key]() -> Value {
                              if (!Luminophore::visualSettings())
                                  throw std::runtime_error("visual settings unavailable");
                              const auto& v = Luminophore::visualSettings()->current().bundle.settings;
                              if (key == "visual.schema_version")
                                  return int64_t(v.schemaVersion);
                              if (key == "visual.preset")
                                  return v.preset;
                              if (key == "visual.enabled")
                                  return v.enabled;
                              if (key == "visual.breathing")
                                  return v.breathing;
                              return v.intensity;
                          },
                          [key, pending](const Value& v) { pending->at(key) = v; },
                          [] {
                              if (!Luminophore::visualSettings())
                                  throw std::runtime_error("visual settings unavailable");
                          }};
    }
    struct SColorRollback {
        Snapshot                                                                           settings;
        std::map<std::string, std::pair<std::string, NColorManagement::PImageDescription>> images;
    };
    auto colorRollback = std::make_shared<SColorRollback>();
    auto colorApplied  = std::make_shared<Snapshot>(initial);
    auto lastGeneral   = std::make_shared<Snapshot>(initial);
    for (auto& [key, destination] : slots) {
        const auto write  = destination.write;
        destination.write = [key, write, pending](const Value& value) {
            write(value);
            pending->at(key) = value;
        };
    }
    auto refresh = [pending, motionCurrent, lastGeneral, colorRollback, colorApplied](bool restoring) {
        if (!Animation::mgr() || !Config::animationTree() || !Luminophore::visualSettings() || !Luminophore::shellProjection())
            throw std::runtime_error("semantic settings consumers unavailable");
        Animation::mgr()->addBezierWithName("luminophore-settings-quick", {0.15, 0.0}, {0.1, 1.0});
        Animation::mgr()->addBezierWithName("luminophore-settings-easeInOutCubic", {0.65, 0.05}, {0.36, 1.0});
        Hyprutils::Animation::SSpringCurve spring;
        spring.mass      = 1;
        spring.stiffness = 500;
        spring.damping   = 35;
        Animation::mgr()->addSpringWithName("luminophore-settings-easy", spring);
        for (const auto& node : motionNodes(*pending))
            Config::animationTree()->setConfigForNode(node.name, node.enabled, node.speed, ownedCurve(node.curve), node.style);
        *motionCurrent       = *pending;
        const auto  previous = Luminophore::visualSettings()->revision();
        const auto& resolved = Luminophore::visualSettings()->apply(visual(*pending));
        if (resolved.recovery != Luminophore::eVisualRecovery::NONE || resolved.bundle.settings != visual(*pending))
            throw std::runtime_error("visual settings rejected");
        if (previous != Luminophore::visualSettings()->revision())
            Luminophore::shellProjection()->visualSettingsChanged();
        const auto changed        = [&](const std::string& key) { return pending->at(key) != lastGeneral->at(key); };
        const bool zoomChanged    = changed("compositor.zoom_factor");
        const bool idleChanged    = changed("compositor.display_idle_minutes");
        const bool primaryChanged = changed("compositor.primary_selection");
        const bool scaleChanged   = changed("compositor.xwayland_native_pixels");
        const bool shaderChanged  = changed("compositor.screen_shader");
        const bool cmChanged      = pending->at("compositor.auto_hdr") != colorApplied->at("compositor.auto_hdr") ||
            pending->at("compositor.sdr_transfer") != colorApplied->at("compositor.sdr_transfer") || pending->at("compositor.icc_vcgt") != colorApplied->at("compositor.icc_vcgt");
        // Record the attempted side effects before applying them, so rollback replays them.
        *lastGeneral = *pending;
        if (idleChanged)
            Luminophore::displayIdleController()->settingsChanged();
        if (primaryChanged && PROTO::primarySelection && g_pSeatManager)
            PROTO::primarySelection->settingsChanged();
        if (scaleChanged) {
            State::monitorLayoutController()->arrange();
            for (const auto& window : Desktop::windowState()->windows()) {
                if (!window->m_isMapped || !window->m_isX11)
                    continue;
                window->updateSurfaceScaleTransformDetails(true);
                window->sendWindowSize(true);
            }
        }
        if (cmChanged) {
            const auto sameColor = [&](const Snapshot& a, const Snapshot& b) {
                return !a.empty() && a.at("compositor.auto_hdr") == b.at("compositor.auto_hdr") && a.at("compositor.sdr_transfer") == b.at("compositor.sdr_transfer") &&
                    a.at("compositor.icc_vcgt") == b.at("compositor.icc_vcgt");
            };
            SColorRollback                                             previous{.settings = *colorApplied};
            std::map<std::string, NColorManagement::PImageDescription> prepared;
            for (const auto& monitor : State::monitorState()->monitors()) {
                const auto& file                 = monitor->m_activeMonitorRule.m_iccFile;
                previous.images[monitor->m_name] = {file, monitor->m_imageDescription};
                const auto cached                = colorRollback->images.find(monitor->m_name);
                if (restoring && sameColor(colorRollback->settings, *pending) && cached != colorRollback->images.end() && cached->second.first == file)
                    prepared[monitor->m_name] = cached->second.second;
                else if (!file.empty()) {
                    const auto image = NColorManagement::SImageDescription::fromICC(file);
                    if (!image)
                        throw std::runtime_error("ICC refresh failed: " + image.error());
                    const auto description = NColorManagement::CImageDescription::from(*image);
                    if (!description)
                        throw std::runtime_error("ICC image description rejected");
                    prepared[monitor->m_name] = description;
                }
            }
            // Only publish after every ICC has been prepared; rollback retains exact previous transforms.
            *colorRollback = std::move(previous);
            *colorApplied  = *pending;
            g_pHyprRenderer->clearCMSettingsCache();
            for (const auto& monitor : State::monitorState()->monitors()) {
                if (prepared.contains(monitor->m_name)) {
                    monitor->m_imageDescription = prepared.at(monitor->m_name);
                    if (PROTO::colorManagement)
                        PROTO::colorManagement->onMonitorImageDescriptionChanged(monitor);
                } else
                    monitor->applyCMType(monitor->m_cmType, monitor->m_sdrEotf);
                monitor->m_needsHDRupdate = true;
                monitor->m_previousFSWindow.reset();
            }
        }
        using namespace Config::Supplementary;
        if (!refresher())
            throw std::runtime_error("config refresher unavailable");
        refresher()->scheduleRefresh(REFRESH_WINDOW_STATES | REFRESH_BLUR_FB | REFRESH_MONITOR_STATES | REFRESH_INPUT_DEVICES | (zoomChanged ? REFRESH_CURSOR_ZOOMS : 0) |
                                     (shaderChanged ? REFRESH_SCREEN_SHADER : 0));
        if (refresher()->executeScheduledRefreshImmediately() != 0)
            throw std::runtime_error("config refresh not executed");
    };
    if (initialize) {
        for (const auto& [key, slot] : slots) {
            if (slot.validate)
                slot.validate(initial.at(key));
            if (slot.writable)
                slot.writable();
        }
        for (const auto& [key, slot] : slots)
            slot.write(initial.at(key));
        refresh(false);
    }
    return std::make_shared<CRuntimeSettingsAdapter>(initial, std::move(slots), [refresh] { refresh(false); }, [refresh] { refresh(true); });
}

std::unique_ptr<CSettingsParticipant> Luminophore::Settings::makeRuntimeParticipant(const std::string& epoch, const std::string& generation, const Snapshot& initial) {
    auto adapter = makeRuntimeAdapter(initial);
    return std::make_unique<CSettingsParticipant>(
        epoch, generation, initial, [adapter](const Snapshot& candidate) { adapter->apply(candidate); }, [adapter] { return adapter->read(); },
        [adapter](const Snapshot& values) { adapter->restore(values); });
}

void Luminophore::Settings::initSettingsService() {
    settingsService() = std::make_unique<CSettingsService>(
        settingsFixtureRoot(),
        [](const std::string& epoch, const SGeneration& boot) {
            if ((!boot.monitors.empty() || !boot.devices.empty()) && Config::mgr()->type() != Config::CONFIG_LUMINOPHORE)
                throw std::runtime_error("monitor generations require the native settings manager");
            auto adapter = makeRuntimeAdapter(boot.values, true);
            return std::make_shared<CSettingsParticipant>(
                epoch, boot.id, boot.values, [adapter](const Snapshot& values) { adapter->apply(values); }, [adapter] { return adapter->read(); },
                [adapter](const Snapshot& values) { adapter->restore(values); });
        },
        [](const MonitorSettings& boot) { return Config::mgr()->type() == Config::CONFIG_LUMINOPHORE ? makeMonitorRuntime(boot) : SMonitorRuntime{}; },
        [](const SGeneration& boot) { return Config::mgr()->type() == Config::CONFIG_LUMINOPHORE ? makeDesktopRuntime(boot) : SInputRuntime{}; });
}
