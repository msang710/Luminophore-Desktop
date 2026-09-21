-- Small bridge from luminophore-shell's TOML source of truth to Hyprland's Lua config.
-- It intentionally exposes only the approved compositor/motion allowlist.

local M = {}

local DEFAULTS = {
    default_view_columns = 2,
    default_view_rows = 2,
    gaps_in = 0,
    gaps_out = 0,
    border_size = 5,
    rounding = 14,
    active_opacity = 1.0,
    inactive_opacity = 1.0,
    dim_special = 0.3,
    blur_enabled = true,
    blur_size = 5,
    blur_passes = 1,
    vrr = 3,
}

local MOTION_DEFAULTS = { enabled = true, preset = "balanced", speed = 1.0 }

local function default_config_path()
    local root = os.getenv("LUMINOPHORE_CONFIG_ROOT")
    if root and root ~= "" then return root .. "/shell.toml" end
    local config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home and config_home ~= "" then
        return config_home .. "/hypr/config/luminophore_shell/config.toml"
    end
    local home = os.getenv("HOME")
    if home and home ~= "" then
        return home .. "/.config/hypr/config/luminophore_shell/config.toml"
    end
    return "luminophore_shell/config.toml"
end

local function scalar(raw)
    if raw == "true" then return true end
    if raw == "false" then return false end
    local quoted = raw:match('^"([^"]+)"$')
    if quoted then return quoted end
    return tonumber(raw)
end

local function read(path)
    -- Portable sessions compile with Python's TOML parser before startup.
    -- Importing these values has no compositor side effects; the entrypoint
    -- invokes apply() after its source-owned modules have loaded.
    if not path and os.getenv("LUMINOPHORE_CONFIG_ROOT") then
        return require("config.luminophore_owned")
    end
    local result = { compositor = {}, motion = {} }
    for key, value in pairs(DEFAULTS) do result.compositor[key] = value end
    for key, value in pairs(MOTION_DEFAULTS) do result.motion[key] = value end
    local handle = io.open(path or default_config_path(), "r")
    if not handle then
        return result
    end

    local section = ""
    for line in handle:lines() do
        local header = line:match("^%s*%[([^%]]+)%]%s*")
        if header then
            section = header
        elseif section == "compositor" or section == "motion" then
            local key, raw = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
            if key and result[section][key] ~= nil then
                local value = scalar(raw)
                if value ~= nil then result[section][key] = value end
            end
        end
    end
    handle:close()
    return result
end

function M.compositor(path) return read(path).compositor end
function M.motion(path) return read(path).motion end

return M
