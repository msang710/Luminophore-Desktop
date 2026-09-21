-- Minimal compositor graph for the product-owned login session.  The greeter
-- does not load desktop autostart, user overrides, wallpaper, or shell units.
hl.monitor({
    output = "",
    mode = "preferred",
    position = "auto",
    scale = 1,
})

hl.config({
    misc = {
        disable_hyprland_logo = true,
        disable_splash_rendering = true,
        force_default_wallpaper = 0,
        background_color = "rgba(0A0D12ff)",
    },
    decoration = {
        blur = { enabled = true, size = 5, passes = 1 },
    },
    animations = { enabled = false },
})

hl.layer_rule({
    name = "luminophore-greeter-blur",
    match = { namespace = "^luminophore-shell-login$" },
    blur = true,
    ignore_alpha = 0.08,
})
