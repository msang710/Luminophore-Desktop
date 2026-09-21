-- Look and feel configuration

local luminophore_settings = require("config.luminophore_settings")
local compositor = luminophore_settings.compositor()
local theme = require("config.luminophore_theme")

hl.config({
    general = {
        gaps_in = compositor.gaps_in,
        gaps_out = compositor.gaps_out,
        border_size = compositor.border_size,
        extend_border_grab_area = 10,
        resize_on_border_inner_area = 5,
        resize_on_border = true,
        col = {
            active_border = theme.primary,
            inactive_border = theme.surface_container,
        },
    },
    decoration = {
        dim_special = compositor.dim_special,
        rounding = compositor.rounding,
        active_opacity = compositor.active_opacity,
        inactive_opacity = compositor.inactive_opacity,
        fullscreen_opacity = 1,
        blur = {
            enabled = compositor.blur_enabled,
            size = compositor.blur_size,
            passes = compositor.blur_passes,
            special = true,
        },
    },
})
