-- Workspace rules wiki https://wiki.hypr.land/Configuring/Basics/Workspace-Rules/
-- Each physical monitor owns one persistent base desktop. Numeric workspace
-- IDs remain internal protocol identifiers and are not a user-facing model.
hl.workspace_rule({ workspace = "name:luminophore-base-" .. LEFT_MONITOR,  monitor = LEFT_MONITOR,  persistent = true })
hl.workspace_rule({ workspace = "name:luminophore-base-" .. RIGHT_MONITOR, monitor = RIGHT_MONITOR, persistent = true })
