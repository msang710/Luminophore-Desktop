-- The release owns the executable configuration graph. The user directory is
-- searched only for value modules and the explicit final override module.
local release = assert(os.getenv("LUMINOPHORE_RELEASE_ROOT"))
local user = os.getenv("LUMINOPHORE_CONFIG_ROOT")
if user and user ~= "" then
    package.path = user .. "/?.lua;" .. package.path
end
package.path = release .. "/config/?.lua;" .. package.path

local source = release .. "/config/config/"
local settings = assert(dofile(source .. "luminophore_settings.lua"))
package.loaded["config.luminophore_settings"] = settings

for _, name in ipairs({
    "animations", "autostart", "colors", "decorations", "variables",
    "environment", "inputs", "binds", "misc", "windowrules", "monitors",
    "workspaces",
}) do
    dofile(source .. name .. ".lua")
end

assert(require("config.luminophore_owned")).apply()
assert(require("config.luminophore_user")).apply()
