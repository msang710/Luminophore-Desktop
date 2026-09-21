-- Explicit user compositor overrides run after release defaults and generated
-- settings. Keep structural module loading in the immutable release entrypoint.
local M = {}

function M.apply()
end

return M
