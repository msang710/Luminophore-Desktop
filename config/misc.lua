local compositor = require("config.luminophore_settings").compositor()

local misc = {
    disable_hyprland_logo = true,
    disable_splash_rendering = true,
    force_default_wallpaper = 0,
    background_color = "rgba(00000000)",
    col = {
        splash = CACHYLGREEN,
    },
    middle_click_paste = false,
    enable_swallow = true,
    swallow_regex = "(kitty|ghostty|[Kk]onsole|Alacritty|gnome-terminal|xfce[0-9]?-terminal)",
    vrr = compositor.vrr,
}

-- New board options are optional until the corresponding compositor is active.
local boardOptions, defaultColumns = pcall(hl.get_config, "misc:luminophore_default_view_columns")
if boardOptions and type(defaultColumns) == "number" then
    misc.luminophore_default_view_columns = compositor.default_view_columns or 2
    misc.luminophore_default_view_rows = compositor.default_view_rows or 2
end

-- This option belongs to the LUMINOPHORE compositor fork.  Keep the shared config
-- loadable by the currently running system Hyprland until the session switch.
if os.getenv("LUMINOPHORE_COMPOSITOR") == "1" then
    misc.luminophore_monitor_idle_minutes = 30
end

-- A pending layout always loads the last confirmed position map after a crash.
local supported, position_config = pcall(hl.get_config, "misc:luminophore_monitor_layout")
if supported and type(position_config) == "string" then
    local source = debug.getinfo(1, "S").source:sub(2):match("^(.*[/])") or ""
    local pending = source .. "luminophore_monitor_layout.pending.lua"
    local file = io.open(pending, "r")
    local selected = source .. "luminophore_monitor_layout.lua"
    if file then file:close(); selected = pending end
    local ok, value = pcall(dofile, selected)
    if ok and type(value) == "string" then misc.luminophore_monitor_layout = value end
end

hl.config({
    misc = misc,
    xwayland = {
        force_zero_scaling = true
    },
    ecosystem = {
        no_update_news = true,
        no_donation_nag = true,
    },
})
