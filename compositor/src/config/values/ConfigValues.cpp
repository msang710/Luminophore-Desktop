#include "ConfigValues.hpp"

using namespace Config;
using namespace Config::Values;

template <typename T>
static std::string opt(std::optional<T> x) {
    if (x)
        return std::format("{}", x.value());
    return "null";
}

template <>
std::string opt<Values::OptionMap>(std::optional<Values::OptionMap> x) {
    if (x) {
        std::string json = "[";
        for (const auto& [k, v] : *x) {
            json += std::format("{{ \"{}\": {} }},", k, v);
        }
        if (!json.empty())
            json.pop_back();
        json += "]";
        return json;
    }
    return "null";
}

static std::string jsonify(SP<IValue> v) {

    if (auto x = dc<CBoolValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": {},
        "current": {}
    }},)#",
            x->name(), x->description(), x->defaultVal(), x->value());
    }

    if (auto x = dc<CIntValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": {},
        "current": {},
        "min": {},
        "max": {},
        "map": {}
    }},)#",
            x->name(), x->description(), x->defaultVal(), x->value(), opt(x->m_min), opt(x->m_max), opt(x->m_map));
    }

    if (auto x = dc<CFloatValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": {},
        "current": {},
        "min": {},
        "max": {}
    }},)#",
            x->name(), x->description(), x->defaultVal(), x->value(), opt(x->m_min), opt(x->m_max));
    }

    if (auto x = dc<CCssGapValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": "{}",
        "current": "{}",
        "min": {},
        "max": {}
    }},)#",
            x->name(), x->description(), x->defaultVal().toString(), x->value().toString(), opt(x->m_min), opt(x->m_max));
    }

    if (auto x = dc<CFontWeightValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": "{}",
        "current": "{}"
    }},)#",
            x->name(), x->description(), x->defaultVal().toString(), x->value().toString());
    }

    if (auto x = dc<CGradientValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": "{}",
        "current": "{}"
    }},)#",
            x->name(), x->description(), x->defaultVal().toString(), x->value().toString());
    }

    if (auto x = dc<CStringValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": "{}",
        "current": "{}"
    }},)#",
            x->name(), x->description(), x->defaultVal(), x->value());
    }

    if (auto x = dc<CVec2Value*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": [{}, {}],
        "current": [{}, {}]
    }},)#",
            x->name(), x->description(), x->defaultVal().x, x->defaultVal().y, x->value().x, x->value().y);
    }

    if (auto x = dc<CColorValue*>(v.get()); x) {
        return std::format(
            R"#(
    {{
        "name": "{}",
        "description": "{}",
        "default": "{:x}",
        "current": "{:x}"
    }},)#",
            x->name(), x->description(), x->defaultVal(), x->value());
    }

    Log::logger->log(Log::ERR, "values/jsonify: invalid value {}", v->name());
    return "{},";
}

std::string Values::getAsJson() {
    std::string json = "[\n";
    for (const auto& v : CONFIG_VALUES) {
        json += jsonify(v);
    }
    json.pop_back();
    json += "\n]";
    return json;
}

std::vector<SP<IValue>> Values::getConfigValues() {
#define MS makeConfigValue

    return std::vector<SP<IValue>>{

        /*
         * general:
         */

        MS<Int>("general:border_size", "size of the border around windows", 1, {.min = 0, .max = 20, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<CssGap>("general:gaps_in", "gaps between windows", 5, {.refresh = Supplementary::REFRESH_LAYOUTS}),
        MS<CssGap>("general:gaps_out", "gaps between windows and monitor edges", 20, {.refresh = Supplementary::REFRESH_LAYOUTS}),
        MS<CssGap>("general:float_gaps", "gaps between windows and monitor edges for floating windows", 0, {.refresh = Supplementary::REFRESH_LAYOUTS}),
        MS<Gradient>("general:col.inactive_border", "border color for inactive windows", CHyprColor{0xff444444}, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Gradient>("general:col.active_border", "border color for the active window", CHyprColor{0xffffffff}, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Bool>("general:no_focus_fallback", "if true, will not fall back to the next available window when moving focus in a direction where no window was found", false),
        MS<Bool>("general:resize_on_border", "enables resizing windows by clicking and dragging on borders and gaps", false),
        MS<Int>("general:extend_border_grab_area", "extends the area around the border where you can click and drag on, only used when general:resize_on_border is on.", 15,
                {.min = 0, .max = 100}),
        MS<Int>("general:resize_on_border_inner_area", "extends the resize grab area into the window without requiring a visible compositor border.", 0, {.min = 0, .max = 100}),
        MS<Bool>("general:hover_icon_on_border", "show a cursor icon when hovering over borders, only used when general:resize_on_border is on.", true),
        MS<Bool>("general:allow_tearing", "master switch for allowing tearing to occur.", false),
        MS<Int>("general:resize_corner", "force floating windows to use a specific corner when being resized (1-4 going clockwise from top left, 0 to disable)", 0,
                {.min = 0, .max = 4, .map = OptionMap{{"disable", 0}, {"top_left", 1}, {"top_right", 2}, {"bottom_right", 3}, {"bottom_left", 4}}}),
        MS<Bool>("general:snap:enabled", "enable snapping for floating windows", false),
        MS<Int>("general:snap:window_gap", "minimum gap in pixels between windows before snapping", 10, {.min = 0, .max = 100}),
        MS<Int>("general:snap:monitor_gap", "minimum gap in pixels between window and monitor edges before snapping", 10, {.min = 0, .max = 100}),
        MS<Bool>("general:snap:border_overlap", "if true, windows snap such that only one border's worth of space is between them", false),
        MS<Bool>("general:snap:respect_gaps", "if true, snapping will respect gaps between windows", false),
        MS<Bool>("general:modal_parent_blocking", "if true, parent windows of modals will not be interactive.", true),
        MS<String>("general:locale", "overrides the system locale", ""),

        /*
         * decoration:
         */

        MS<Int>("decoration:rounding", "rounded corners' radius (in layout px)", 0,
                {.min = 0, .max = 20, .refresh = Supplementary::REFRESH_WINDOW_STATES | Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:rounding_power", "rounding power of corners (2 is a circle)", 2,
                  {.min = 2, .max = 10, .refresh = Supplementary::REFRESH_WINDOW_STATES | Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:active_opacity", "opacity of active windows.", 1, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Float>("decoration:inactive_opacity", "opacity of inactive windows.", 1, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Float>("decoration:fullscreen_opacity", "opacity of fullscreen windows.", 1, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Bool>("decoration:shadow:enabled", "enable drop shadows on windows", true, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Int>("decoration:shadow:range", "Shadow range (size) in layout px", 4, {.min = 0, .max = 100, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Int>("decoration:shadow:render_power", "in what power to render the falloff (more power, the faster the falloff)", 3,
                {.min = 1, .max = 4, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Bool>("decoration:shadow:sharp", "whether the shadow should be sharp or not.", false, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Gradient>("decoration:shadow:color", "shadow's color. Alpha dictates shadow's opacity.", CHyprColor{0xee1a1a1a}, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Gradient>("decoration:shadow:color_inactive", "inactive shadow color. (if not set, will fall back to col.shadow)", -1,
                     {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Vec2>("decoration:shadow:offset", "shadow's rendering offset.", Config::VEC2{},
                 {.validator = vec2Range(-250, -250, 250, 250), .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Float>("decoration:shadow:scale", "shadow's scale.", 1, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Bool>("decoration:glow:enabled", "enable inner glow on windows", false, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Int>("decoration:glow:range", "glow range (size) in layout px", 10, {.min = 0, .max = 100, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Int>("decoration:glow:render_power", "in what power to render the falloff (more power, the faster the falloff)", 3,
                {.min = 1, .max = 4, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Gradient>("decoration:glow:color", "glow's color. Alpha dictates glow's opacity.", CHyprColor{0xee33ccff}, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Gradient>("decoration:glow:color_inactive", "inactive glow color. (if not set, will fall back to decoration:glow:color)", -1,
                     {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Bool>("decoration:dim_modal", "enables dimming of parents of modal windows", true, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Bool>("decoration:dim_inactive", "enables dimming of inactive windows", false, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Float>("decoration:dim_strength", "how much inactive windows should be dimmed", 0.5, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Float>("decoration:dim_special", "how much to dim the rest of the screen by when a special workspace is open.", 0.2,
                  {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Float>("decoration:dim_around", "how much the dimaround window rule should dim by.", 0.4, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<String>("decoration:screen_shader", "a path to a custom shader to be applied at the end of rendering.", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_SCREEN_SHADER}),
        MS<Bool>("decoration:border_part_of_window", "whether the border should be treated as a part of the window.", true),

        /*
         * blur:
         */

        MS<Bool>("decoration:blur:enabled", "enable kawase window background blur", true, {.refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Int>("decoration:blur:size", "blur size (distance)", 8, {.min = 0, .max = 100, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Int>("decoration:blur:passes", "the amount of passes to perform", 1, {.min = 0, .max = 10, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Bool>("decoration:blur:ignore_opacity", "make the blur layer ignore the opacity of the window", true, {.refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Bool>("decoration:blur:new_optimizations", "whether to enable further optimizations to the blur.", true, {.refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Bool>("decoration:blur:xray", "if enabled, floating windows will ignore tiled windows in their blur.", false, {.refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:blur:noise", "how much noise to apply.", 0.0117, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:blur:contrast", "contrast modulation for blur.", 0.8916, {.min = 0, .max = 2, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:blur:brightness", "brightness modulation for blur.", 1, {.min = 0, .max = 2, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:blur:vibrancy", "Increase saturation of blurred colors.", 0.1696, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:blur:vibrancy_darkness", "How strong the effect of vibrancy is on dark areas.", 0, {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Bool>("decoration:blur:special", "whether to blur behind the special workspace (note: expensive)", false, {.refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Bool>("decoration:blur:popups", "whether to blur popups (e.g. right-click menus)", false, {.refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:blur:popups_ignorealpha", "works like ignorealpha in layer rules. If pixel opacity is below set value, will not blur.", 0.2,
                  {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Bool>("decoration:blur:input_methods", "whether to blur input methods (e.g. fcitx5)", false, {.refresh = Supplementary::REFRESH_BLUR_FB}),
        MS<Float>("decoration:blur:input_methods_ignorealpha", "works like ignorealpha in layer rules. If pixel opacity is below set value, will not blur.", 0.2,
                  {.min = 0, .max = 1, .refresh = Supplementary::REFRESH_BLUR_FB}),

        MS<Bool>("decoration:motion_blur:enabled", "enable motion blur for moving and resizing windows", false, {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Int>("decoration:motion_blur:samples", "amount of samples used for motion blur", 7, {.min = 1, .max = 64, .refresh = Supplementary::REFRESH_WINDOW_STATES}),

        /*
         * animations:
         */

        MS<Bool>("animations:enabled", "enable animations", true),

        /*
         * input:
         */

        MS<String>("input:kb_model", "Appropriate XKB keymap parameter.", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:kb_layout", "Appropriate XKB keymap parameter", "us", {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:kb_variant", "Appropriate XKB keymap parameter", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:kb_options", "Appropriate XKB keymap parameter", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:kb_rules", "Appropriate XKB keymap parameter", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:kb_snapshot", "Imported immutable XKB keymap", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:kb_file", "Appropriate XKB keymap file", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:numlock_by_default", "Engage numlock by default.", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:resolve_binds_by_sym", "Determines how keybinds act when multiple layouts are used.", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:repeat_rate", "The repeat rate for held-down keys, in repeats per second.", 25, {.min = 0, .max = 200, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:repeat_delay", "Delay before a held-down key is repeated, in milliseconds.", 600, {.min = 0, .max = 2000}),
        MS<Float>("input:sensitivity", "Sets the mouse input sensitivity. Value is clamped to the range -1.0 to 1.0.", 0,
                  {.min = -1, .max = 1, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:accel_profile", "Sets the cursor acceleration profile. [adaptive/flat/custom]", STRVAL_EMPTY,
                   {.validator = strChoice({"adaptive", "flat", "custom"}), .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:force_no_accel", "Force no cursor acceleration.", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:rotation", "Sets the rotation of a device in degrees clockwise. Value is clamped to the range 0 to 359.", 0,
                {.min = 0, .max = 359, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:left_handed", "Switches RMB and LMB", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:scroll_points", "Sets the scroll acceleration profile, when accel_profile is set to custom.", STRVAL_EMPTY,
                   {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:scroll_method", "Sets the scroll method. [2fg/edge/on_button_down/no_scroll]", STRVAL_EMPTY,
                   {.validator = strChoice({"2fg", "edge", "on_button_down", "no_scroll"}), .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:scroll_button", "Sets the scroll button. 0 means default.", 0, {.min = 0, .max = 300, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:scroll_button_lock", "If the scroll button lock is enabled, the button does not need to be held down.", false,
                 {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Float>("input:scroll_factor", "Multiplier added to scroll movement for external mice.", 1, {.min = 0, .max = 2, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:natural_scroll", "Inverts scrolling direction.", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:follow_mouse", "Specify if and how cursor movement should affect window focus.", 1,
                {.min = 0, .max = 3, .map = OptionMap{{"disabled", 0}, {"follow", 1}, {"detached", 2}, {"separate", 3}}, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Float>("input:follow_mouse_threshold", "The smallest distance in logical pixels the mouse needs to travel for the window under it to get focused.", 0),
        MS<Int>("input:focus_on_close", "Controls the window focus behavior when a window is closed.", 0,
                {.min = 0, .max = 2, .map = OptionMap{{"next", 0}, {"cursor", 1}, {"mru", 2}}, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:mouse_refocus", "if disabled, mouse focus won't switch to the hovered window unless the mouse crosses a window boundary when follow_mouse=1.", true),
        MS<Int>("input:float_switch_override_focus",
                "If enabled (1 or 2), focus will change to the window under the cursor when changing from tiled-to-floating and vice versa. If 2, focus will also follow mouse on "
                "float-to-float switches.",
                1, {.min = 0, .max = 2, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:off_window_axis_events", "How to handle axis events around a focused window.", 1,
                {.min = 0, .max = 3, .map = OptionMap{{"ignore", 0}, {"send", 1}, {"clamp", 2}, {"warp", 3}}, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:emulate_discrete_scroll", "Emulates discrete scrolling from high resolution scrolling events.", 1,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"non_standard", 1}, {"force_all", 2}}, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:follow_mouse_shrink",
                "Shrinks the inactive window hitboxes used for focus detection by the specified number of pixels. This creates a dead zone in gaps between windows where moving "
                "the cursor will not change focus. Works only with follow_mouse = 1.",
                0, {.min = 0, .max = 300, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),

        /*
         * input:touchpad:
         */

        MS<Bool>("input:touchpad:disable_while_typing", "Disable the touchpad while typing.", true, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchpad:natural_scroll", "Inverts scrolling direction.", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Float>("input:touchpad:scroll_factor", "Multiplier applied to the amount of scroll movement.", 1, {.min = 0, .max = 2, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchpad:middle_button_emulation", "Sending LMB and RMB simultaneously will be interpreted as a middle click.", false,
                 {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:touchpad:tap_button_map", "Sets the tap button mapping for touchpad button emulation. [lrm/lmr]", STRVAL_EMPTY,
                   {.validator = strChoice({"lrm", "lmr"}), .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchpad:clickfinger_behavior", "Button presses with 1, 2, or 3 fingers will be mapped to LMB, RMB, and MMB respectively.", false,
                 {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchpad:tap-to-click", "Tapping on the touchpad with 1, 2, or 3 fingers will send LMB, RMB, and MMB respectively.", true,
                 {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:touchpad:drag_lock", "When enabled, lifting the finger off while dragging will not drop the dragged item.", 0,
                {.min = 0, .max = 2, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchpad:tap-and-drag", "Sets the tap and drag mode for the touchpad", true, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchpad:flip_x", "Inverts the horizontal movement of the touchpad", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchpad:flip_y", "Inverts the vertical movement of the touchpad", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:touchpad:drag_3fg", "Whether to use 3 or 4 finger drag.", 0,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"3_finger", 1}, {"4_finger", 2}}, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),

        /*
         * input:touchdevice:
         */

        MS<Int>("input:touchdevice:transform", "Transform the input from touchdevices.", 0, {.min = 0, .max = 6, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:touchdevice:output", "The monitor to bind touch devices.", "[[Auto]]", {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:touchdevice:enabled", "Whether input is enabled for touch devices.", true, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),

        /*
         * input:virtualkeyboard:
         */

        MS<Int>("input:virtualkeyboard:share_states", "Unify key down states and modifier states with other keyboards.", 2,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"only_non_ime", 2}}, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:virtualkeyboard:release_pressed_on_close", "Release all pressed keys by virtual keyboard on close.", false,
                 {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),

        /*
         * input:tablet:
         */

        MS<Int>("input:tablet:transform", "transform the input from tablets.", 0, {.min = 0, .max = 6, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<String>("input:tablet:output", "the monitor to bind tablets.", STRVAL_EMPTY, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Vec2>("input:tablet:region_position", "position of the mapped region in monitor layout.", Config::VEC2{},
                 {.validator = vec2Range(-20000, -20000, 20000, 20000), .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:tablet:absolute_region_position", "whether to treat the region_position as an absolute position in monitor layout.", false,
                 {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Vec2>("input:tablet:region_size", "size of the mapped region.", Config::VEC2{},
                 {.validator = vec2Range(-100, -100, 4000, 4000), .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:tablet:relative_input", "whether the input should be relative", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Bool>("input:tablet:left_handed", "if enabled, the tablet will be rotated 180 degrees", false, {.refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Vec2>("input:tablet:active_area_size", "size of tablet's active area in mm", Config::VEC2{},
                 {.validator = vec2Range(0, 0, 500, 500), .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Vec2>("input:tablet:active_area_position", "position of the active area in mm", Config::VEC2{},
                 {.validator = vec2Range(0, 0, 500, 500), .refresh = Supplementary::REFRESH_INPUT_DEVICES}),

        /*
         * input:tablettool:
         */

        MS<Int>("input:tablettool:eraser_button_mode",
                "Change the eraser button behavior on the tool. When set to 0, use the default hardware behavior of the tool. "
                "When set to 1, the eraser button on the tool sends a button event instead.",
                0, {.min = 0, .max = 6, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Int>("input:tablettool:eraser_button_override",
                "Set a button to be button event when eraser_button_mode is set to 1. Has to be an int, cannot be a string. Must be a valid button (e.g. BTN_STYLUS) "
                "excluding fake buttons (e.g. BTN_TOOL_*) and keys (KEY_*). Check wev if you have any doubts regarding the ID. 0 means default.",
                0, {.min = 0, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Float>("input:tablettool:pressure_range_min",
                  "Set the minimum pressure range for the tool, a negative number will set the default minimum pressure value. This is usually 0.0", -1.0,
                  {.min = -1.0, .max = 1.0, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Float>("input:tablettool:pressure_range_max",
                  "Set the maximum pressure range for the tool, a negative number will set the default maximum pressure value. This is usually 1.0", -1.0,
                  {.min = -1.0, .max = 1.0, .refresh = Supplementary::REFRESH_INPUT_DEVICES}),

        /*
         * gestures:
         */

        MS<Int>("gestures:close_max_timeout", "Timeout for closing windows with the close gesture, in ms.", 1000, {.min = 10, .max = 2000}),
        /*
         * misc:
         */

        MS<Bool>("misc:disable_hyprland_logo", "disables the random Hyprland logo / anime girl background. :(", false),
        MS<Bool>("misc:disable_splash_rendering", "disables the Hyprland splash rendering.", false),
        MS<Color>("misc:col.splash", "Changes the color of the splash text.", 0x55ffffff),
        MS<String>("misc:font_family", "Set the global default font to render the text.", "Sans"),
        MS<String>("misc:splash_font_family", "Changes the font used to render the splash text.", "[[EMPTY]]"),
        MS<Int>("misc:force_default_wallpaper", "Force any of the 3 default wallpapers. [-1/0/1/2]", -1, {.min = -1, .max = 2}),
        MS<Int>("misc:vrr", "controls the VRR (Adaptive Sync) of your monitors", 0,
                {.min = 0, .max = 3, .map = OptionMap{{"off", 0}, {"on", 1}, {"fullscreen", 2}, {"fullscreen_game", 3}}, .refresh = Supplementary::REFRESH_MONITOR_STATES}),
        MS<Bool>("misc:mouse_move_enables_dpms", "If DPMS is set to off, wake up the monitors if the mouse moves", false),
        MS<Bool>("misc:key_press_enables_dpms", "If DPMS is set to off, wake up the monitors if a key is pressed.", false),
        MS<Int>("misc:luminophore_default_view_columns", "LUMINOPHORE default view columns; a viewport baseline, never board capacity.", 2, {.min = 1, .max = 64}),
        MS<Int>("misc:luminophore_default_view_rows", "LUMINOPHORE default view rows; a viewport baseline, never board capacity.", 2, {.min = 1, .max = 64}),
        MS<Int>("misc:luminophore_monitor_idle_minutes", "LUMINOPHORE monitor-only idle timeout in minutes. 0 disables it; this never suspends the session or applications.", 0,
                {.min = 0, .max = 1440}),
        MS<Bool>("misc:name_vk_after_proc", "Name virtual keyboards after the processes that create them.", true),
        MS<Bool>("misc:always_follow_on_dnd", "Will make mouse focus follow the mouse when drag and dropping.", true),
        MS<Bool>("misc:layers_hog_keyboard_focus", "If true, will make keyboard-interactive layers keep their focus on mouse move.", true),
        MS<Bool>("misc:animate_manual_resizes", "If true, will animate manual window resizes/moves", false),
        MS<Bool>("misc:animate_mouse_windowdragging", "If true, will animate windows being dragged by mouse.", false),
        MS<Bool>("misc:disable_autoreload", "If true, the config will not reload automatically on save.", false, {.refresh = Supplementary::REFRESH_CONFIG_WATCHER}),
        MS<Bool>("misc:enable_swallow", "Enable window swallowing", false),
        MS<String>("misc:swallow_regex", "The class regex to be used for windows that should be swallowed.", STRVAL_EMPTY),
        MS<String>("misc:swallow_exception_regex", "The title regex to be used for windows that should not be swallowed.", STRVAL_EMPTY),
        MS<Bool>("misc:focus_on_activate", "Whether Hyprland should focus an app that requests to be focused.", false),
        MS<Bool>("misc:mouse_move_focuses_monitor", "Whether mouse moving into a different monitor should focus it", true),
        MS<Bool>("misc:allow_session_lock_restore", "if true, will allow you to restart a lockscreen app in case it crashes.", false),
        MS<Bool>("misc:session_lock_xray", "keep rendering workspaces below your lockscreen", false),
        MS<Bool>("misc:session_lock_blur", "Enable blur for lockscreen. You probably want to enable `session_lock_xray`.", false),
        MS<Color>("misc:background_color", "change the background color.", 0xff111111),
        MS<Bool>("misc:close_special_on_empty", "close the special workspace if the last window is removed", true),
        MS<Int>("misc:on_focus_under_fullscreen", "if there is a fullscreen or maximized window, decide whether a tiled window requested to focus should replace it.", 2,
                {.min = 0, .max = 2, .map = OptionMap{{"ignore", 0}, {"take_over", 1}, {"exit_fullscreen", 2}}}),
        MS<Bool>("misc:exit_window_retains_fullscreen", "if true, closing a fullscreen window makes the next focused window fullscreen", false),
        MS<Bool>("misc:middle_click_paste", "whether to enable middle-click-paste (aka primary selection)", true),
        MS<Int>("misc:render_unfocused_fps", "the maximum limit for renderunfocused windows' fps in the background", 15, {.min = 1, .max = 120}),
        MS<Bool>("misc:disable_xdg_env_checks", "disable the warning if XDG environment is externally managed", false),
        MS<Bool>("misc:disable_hyprland_guiutils_check", "disable the warning if hyprland-guiutils is missing", false),
        MS<Bool>("misc:disable_watchdog_warning", "whether to disable the warning about not using start-hyprland.", false),
        MS<Int>("misc:lockdead_screen_delay", "the delay in ms after the lockdead screen appears.", 1000, {.min = 0, .max = 5000}),
        MS<Bool>("misc:enable_anr_dialog", "whether to enable the ANR (app not responding) dialog when your apps hang", true),
        MS<Int>("misc:anr_missed_pings", "number of missed pings before showing the ANR dialog", 5, {.min = 1, .max = 20}),
        MS<Bool>("misc:screencopy_force_8b", "forces 8 bit screencopy", true),
        MS<Bool>("misc:disable_scale_notification", "disables notification popup when a monitor fails to set a suitable scale", false),
        MS<Bool>("misc:size_limits_tiled", "whether to apply minsize and maxsize rules to tiled windows", false),

        /*
         * binds:
         */

        MS<Bool>("binds:pass_mouse_when_bound", "if disabled, will not pass the mouse events to apps / dragging windows around if a keybind has been triggered.", false),
        MS<Int>("binds:scroll_event_delay", "in ms, how many ms to wait after a scroll event to allow passing another one for the binds.", 300, {.min = 0, .max = 2000}),
        MS<Bool>("binds:hide_special_on_workspace_change", "If enabled, changing the active workspace will hide the special workspace on the monitor.", false),
        MS<Int>("binds:workspace_center_on", "Whether switching workspaces should center the cursor on the workspace (0) or on the last active window (1)", 1,
                {.min = 0, .max = 1}),
        MS<Int>("binds:focus_preferred_method", "sets the preferred focus finding method when using focuswindow/movewindow/etc with a direction.", 0, {.min = 0, .max = 1}),
        MS<Bool>("binds:movefocus_cycles_fullscreen", "If enabled, when on a fullscreen window, movefocus will cycle fullscreen.", false),
        MS<Bool>("binds:disable_keybind_grabbing", "If enabled, apps that request keybinds to be disabled will not be able to do so.", false),
        MS<Bool>("binds:window_direction_monitor_fallback", "If enabled, moving a window or focus over the edge of a monitor with a direction will move it to the next monitor.",
                 true),
        MS<Bool>("binds:allow_pin_fullscreen", "Allows fullscreen to pinned windows, and restore their pinned status afterwards", false),
        MS<Int>("binds:drag_threshold", "Movement threshold in pixels for window dragging and c/g bind flags. 0 to disable.", 0,
                {.min = 0, .max = std::numeric_limits<int>::max()}),

        /*
         * xwayland:
         */

        MS<String>("misc:luminophore_monitor_layout", "Luminophore saved position-only monitor layout", ""),
        MS<Bool>("xwayland:enabled", "allow running applications using X11", true),
        MS<Bool>("xwayland:use_nearest_neighbor", "uses the nearest neighbor filtering for xwayland apps, making them pixelated rather than blurry", true),
        MS<Bool>("xwayland:force_zero_scaling", "forces a scale of 1 on xwayland windows on scaled displays.", false),
        MS<Bool>("xwayland:create_abstract_socket", "Create the abstract Unix domain socket for XWayland", false),

        /*
         * opengl:
         */

        MS<Bool>("opengl:nvidia_anti_flicker", "reduces flickering on nvidia at the cost of possible frame drops on lower-end GPUs.", true),

        /*
         * render:
         */

        MS<Int>("render:direct_scanout", "Enables direct scanout.", 0, {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),
        MS<Bool>("render:expand_undersized_textures", "Whether to expand textures that have not yet resized to be larger.", true),
        MS<Bool>("render:xp_mode", "Disable back buffer and bottom layer rendering.", false),
        MS<Int>("render:ctm_animation", "Whether to enable a fade animation for CTM changes.", 2,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),
        MS<Bool>("render:cm_enabled", "Enable Color Management pipelines (requires restart to fully take effect)", true),
        MS<Bool>("render:send_content_type", "Report content type to allow monitor profile autoswitch", true),
        MS<Int>("render:cm_auto_hdr", "Auto-switch to hdr mode when fullscreen app is in hdr", 1,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"hdr", 1}, {"hdredid", 2}}}),
        MS<Bool>("render:new_render_scheduling", "enable new render scheduling, which should improve FPS on underpowered devices.", false),
        MS<Int>("render:non_shader_cm", "Enable CM without shader.", 3, {.min = 0, .max = 3, .map = OptionMap{{"disable", 0}, {"always", 1}, {"ondemand", 2}, {"ignore", 3}}}),
        MS<String>("render:cm_sdr_eotf", "Default transfer function for displaying SDR apps.", "default"),
        MS<Bool>("render:commit_timing_enabled", "Enable commit timing proto. Requires restart", true),
        MS<Bool>("render:icc_vcgt_enabled", "Enable sending VCGT ramps to KMS with ICC profiles", true),
        MS<Bool>("render:use_shader_blur_blend", "Use experimental blurred bg blending", false),
        MS<Int>("render:use_fp16", "Use experimental internal FP16 buffer.", 2, {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),
        MS<Int>("render:keep_unmodified_copy", "Keep umodified SDR frame copy for sreensharing.", 2,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),
        MS<Int>("render:non_shader_cm_interop", "non_shader_cm interaction with ctm proto (hyprsunset and similar).", 2,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),
        MS<Int>("render:fp16_sdr_tf", "Internal workbuffer transfer function for fp16 in SDR mode", 0, {.min = 0, .max = 1, .map = OptionMap{{"monitor", 0}, {"linear", 1}}}),

        /*
         * cursor:
         */

        MS<Bool>("cursor:invisible", "don't render cursors", false),
        MS<Int>("cursor:no_hardware_cursors", "disables hardware cursors.", 2, {.min = 0, .max = 2, .map = OptionMap{{"Disabled", 0}, {"Enabled", 1}, {"Auto", 2}}}),
        MS<Int>("cursor:no_break_fs_vrr", "disables scheduling new frames on cursor movement for fullscreen apps with VRR enabled.", 2,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),
        MS<Int>("cursor:min_refresh_rate", "minimum refresh rate for cursor movement when no_break_fs_vrr is active.", 24, {.min = 10, .max = 500}),
        MS<Int>("cursor:hotspot_padding", "the padding, in logical px, between screen edges and the cursor", 0, {.min = 0, .max = 20}),
        MS<Float>("cursor:inactive_timeout", "in seconds, after how many seconds of cursor's inactivity to hide it. Set to 0 for never.", 0, {.min = 0, .max = 20}),
        MS<Bool>("cursor:no_warps", "if true, will not warp the cursor in many cases", false),
        MS<Bool>("cursor:persistent_warps", "When a window is refocused, the cursor returns to its last position relative to that window.", false),
        MS<Int>("cursor:warp_on_change_workspace", "Move the cursor to the last focused window after changing the workspace.", 0,
                {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"force", 2}}}),
        MS<String>("cursor:default_monitor", "the name of a default monitor for the cursor to be set to on startup", STRVAL_EMPTY),
        MS<Float>("cursor:zoom_factor", "the factor to zoom by around the cursor. 1 means no zoom.", 1, {.min = 1, .max = 10, .refresh = Supplementary::REFRESH_CURSOR_ZOOMS}),
        MS<Bool>("cursor:zoom_rigid", "whether the zoom should follow the cursor rigidly or loosely", false),
        MS<Bool>("cursor:zoom_disable_aa", "If enabled, when zooming, no antialiasing will be used", false),
        MS<Bool>("cursor:zoom_detached_camera", "Detaches the camera from the mouse when zoomed in", true),
        MS<Bool>("cursor:enable_hyprcursor", "whether to enable hyprcursor support", true),
        MS<Bool>("cursor:hide_on_key_press", "Hides the cursor when you press any key until the mouse is moved.", false),
        MS<Bool>("cursor:hide_on_touch", "Hides the cursor when the last input was a touch input until a mouse input is done.", true),
        MS<Bool>("cursor:hide_on_tablet", "Hides the cursor when the last input was a tablet input until a mouse input is done.", false),
        MS<Int>("cursor:use_cpu_buffer", "Makes HW cursors use a CPU buffer.", 2, {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),
        MS<Bool>("cursor:sync_gsettings_theme", "sync xcursor theme with gsettings", true),
        MS<Bool>("cursor:warp_back_after_non_mouse_input", "warp the cursor back to where it was after using a non-mouse input to move it.", false),

        /*
         * ecosystem:
         */

        MS<Bool>("ecosystem:no_update_news", "disable the popup that shows up when you update hyprland to a new version.", false),
        MS<Bool>("ecosystem:no_donation_nag", "disable the popup that shows up twice a year encouraging to donate.", false),
        MS<Bool>("ecosystem:enforce_permissions", "whether to enable permission control.", false),

        /*
         * debug:
         */

        MS<Bool>("debug:overlay", "print the debug performance overlay.", false),
        MS<Bool>("debug:damage_blink", "flash damaged areas", false),
        MS<Bool>("debug:gl_debugging", "enable OpenGL debugging and error checking.", false),
        MS<Bool>("debug:disable_logs", "disable logging to a file", true),
        MS<Bool>("debug:disable_time", "disables time logging", true),
        MS<Int>("debug:damage_tracking", "redraw only the needed bits of the display.", 2, {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"monitor", 1}, {"full", 2}}}),
        MS<Bool>("debug:enable_stdout_logs", "enables logging to stdout", false),
        MS<Int>("debug:manual_crash", "set to 1 and then back to 0 to crash Hyprland.", 0, {.min = 0, .max = 1}),
        MS<Bool>("debug:suppress_errors", "if true, do not display config file parsing errors.", false),
        MS<Bool>("debug:disable_scale_checks", "disables verification of the scale factors.", false),
        MS<Int>("debug:error_limit", "limits the number of displayed config file parsing errors.", 5, {.min = 0, .max = 20}),
        MS<Int>("debug:error_position", "sets the position of the error bar.", 0, {.min = 0, .max = 1, .map = OptionMap{{"top", 0}, {"bottom", 1}}}),
        MS<Bool>("debug:colored_stdout_logs", "enables colors in the stdout logs.", true),
        MS<Bool>("debug:log_damage", "enables logging the damage.", false),
        MS<Bool>("debug:pass", "enables render pass debugging.", false),
        MS<Bool>("debug:full_cm_proto", "claims support for all cm proto features (requires restart)", false),
        MS<Bool>("debug:ds_handle_same_buffer", "Special case for DS with unmodified buffer", true),
        MS<Bool>("debug:ds_handle_same_buffer_fifo", "Special case for DS with unmodified buffer unlocks fifo", true),
        MS<Bool>("debug:fifo_pending_workaround", "Fifo workaround for empty pending list", false),
        MS<Bool>("debug:render_solitary_wo_damage", "Render solitary window with empty damage", false),
        MS<Bool>("debug:vfr", "controls the VFR status of Hyprland. Do not turn off unless debugging", true),
        MS<Int>("debug:invalidate_fp16", "allow fp16 buffer invalidation.", 1, {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"auto", 2}}}),

        /*
         * experimental:
         */

        MS<Bool>("experimental:wp_cm_1_2", "Allow wp-cm-v1 version 2", true),

        /*
		 * input_capture: 
		 */
        MS<Bool>("input-capture:capture_modifiers", "If enabled, modifiers are also captured and sent to the program", false),
        MS<Bool>("input-capture:enforce_barriers", "If enabled, throw a wayland error when a invalid barrier is received", true),

        /*
         * quirks:
         */

        MS<Int>("quirks:prefer_hdr", "Prefer HDR mode.", 0, {.min = 0, .max = 2, .map = OptionMap{{"disable", 0}, {"enable", 1}, {"gamescope_only", 2}}}),
        MS<Bool>("quirks:skip_non_kms_dmabuf_formats", "Do not report dmabuf formats which cannot be imported into KMS", false),
    };

#undef MS
}
