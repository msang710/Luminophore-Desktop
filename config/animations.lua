-- Default curves and animations, see https://wiki.hypr.land/Configuring/Advanced-and-Cool/Animations/

-- Default beziers
hl.curve("easeOutQuint",   { type = "bezier", points = { {0.23, 1},    {0.32, 1}    } })
hl.curve("easeInOutCubic", { type = "bezier", points = { {0.65, 0.05}, {0.36, 1}    } })
hl.curve("linear",         { type = "bezier", points = { {0, 0},       {1, 1}       } })
hl.curve("almostLinear",   { type = "bezier", points = { {0.5, 0.5},   {0.75, 1}    } })
hl.curve("quick",          { type = "bezier", points = { {0.15, 0},    {0.1, 1}     } })
hl.curve("overshoot",      { type = "bezier", points = { {0.5, 0.9}, {0.1, 1.1}     } })

-- Default springs
hl.curve("easy",           { type = "spring", mass = 1, stiffness = 500, dampening = 35 })
hl.curve("rubber",         { type = "spring", mass = 1, stiffness = 200,  dampening = 15 })

-- Animations
local motion = require("config.luminophore_settings").motion()
local presets = {
    fast = { windows = 2, curve = "quick" },
    balanced = { windows = 3, curve = "quick" },
    smooth = { windows = 5, curve = "easeInOutCubic" },
    custom = { windows = 3, curve = "quick" },
}
local selected = presets[motion.preset] or presets.balanced
local scale = motion.speed or 1.0
hl.animation({ leaf = "global",              enabled = motion.enabled, speed = selected.windows * scale, bezier = selected.curve })
hl.animation({ leaf = "windows",             enabled = motion.enabled, speed = selected.windows * scale, spring = "easy", style = "slide" })
hl.animation({ leaf = "specialWorkspaceIn",  enabled = motion.enabled, speed = 2 * scale, bezier = selected.curve, style = "slide top"})
hl.animation({ leaf = "specialWorkspaceOut", enabled = motion.enabled, speed = 2 * scale, bezier = selected.curve, style = "slide bottom"})
