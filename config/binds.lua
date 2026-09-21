local mainMod = "SUPER"
local luminophoreShell = os.getenv("LUMINOPHORE_RELEASE_ROOT") and "/usr/bin/luminophore-shell"
    or (os.getenv("HOME") .. "/.config/hypr/config/luminophore-shell")
local luminophoreCall = luminophoreShell .. " ctl "
local launchPrefix = "uwsm app -- " -- if you are not using UWSM, make this empty (e.g. "")
local luminophoreCompositor = os.getenv("LUMINOPHORE_COMPOSITOR") == "1"
local compositorControl = luminophoreCompositor
    and (os.getenv("LUMINOPHORE_CONTROL_BIN") or "/usr/bin/luminophorectl")
    or "hyprctl"
local function launchApp(command)
    if luminophoreCompositor then
        return hl.dsp.exec_cmd(luminophoreShell .. " launch -- " .. command)
    end
    return hl.dsp.exec_cmd(launchPrefix .. command)
end
local occupyOutput = luminophoreCompositor
    and hl.dsp.luminophore.occupy_output()
    or hl.dsp.window.fullscreen()
local captureRegion = luminophoreCompositor
    and (luminophoreShell .. " capture region --freeze --clipboard-only")
    or "hyprshot -m region --freeze -s --clipboard-only"
local captureRegionSave = luminophoreCompositor
    and (luminophoreShell .. " capture region --freeze --save")
    or 'hyprshot -m region --freeze -s -o "$(xdg-user-dir PICTURES)"'

-- Implementations are source-owned here.  The generated module may only
-- select chords/flags for this exact action inventory.  Calls are queued so a
-- malformed, partial, duplicated, or foreign payload falls back atomically to
-- every packaged default instead of applying a mixture.
local packaged = {}
local function bind(action_id, packaged_chord, implementation, packaged_flags)
    packaged[#packaged + 1] = {
        action_id = action_id,
        chord = packaged_chord,
        implementation = implementation,
        flags = packaged_flags or {},
    }
end

local function normalized_chord(chord)
    if type(chord) ~= "string" or chord:match("^%s*$") then return nil end
    return chord:upper():gsub("CONTROL", "CTRL"):gsub("%s*+%s*", " + ")
end

local allowed_flags = { locked = true, repeating = true, release = true, non_consuming = true }
local optional_action_ids = {
    ["spatial.undo"] = true,
    ["spatial.redo"] = true,
    ["view.move.left"] = true,
    ["view.move.right"] = true,
    ["view.move.up"] = true,
    ["view.move.down"] = true,
    ["view.adjust.left"] = true,
    ["view.adjust.right"] = true,
    ["view.adjust.up"] = true,
    ["view.adjust.down"] = true,
    ["board.move.left"] = true,
    ["board.move.right"] = true,
    ["board.move.up"] = true,
    ["board.move.down"] = true,
    ["view.wide.toggle"] = true,
    ["view.desktop.toggle"] = true,
    ["window.focus.left"] = true,
    ["window.focus.right"] = true,
    ["window.focus.up"] = true,
    ["window.focus.down"] = true,
}
local function validated_payload()
    local ok, payload = pcall(require, "config.luminophore_bindings")
    if not ok or type(payload) ~= "table" or payload.version ~= 1 or type(payload.bindings) ~= "table" then return nil end
    local rows, triggers, groups, recovery = {}, {}, {}, false
    for _, row in ipairs(payload.bindings) do
        if type(row) ~= "table" or type(row.action_id) ~= "string" or rows[row.action_id] ~= nil then return nil end
        local chord = normalized_chord(row.chord)
        if (row.chord ~= nil and chord == nil) or type(row.flags) ~= "table" then return nil end
        local flags = {}
        for key, value in pairs(row.flags) do
            if not allowed_flags[key] or type(value) ~= "boolean" then return nil end
            flags[key] = value
        end
        if flags.release and flags.repeating then return nil end
        local phase = flags.release and "release" or "press"
        if chord then
            local trigger = chord .. "|" .. phase
            if triggers[trigger] then return nil end
            triggers[trigger] = true
        end
        if row.group_id ~= nil and type(row.group_id) ~= "string" then return nil end
        if row.recovery ~= nil and type(row.recovery) ~= "boolean" then return nil end
        if row.recovery and chord then recovery = true end
        if row.group_id and row.group_id ~= "" then
            groups[row.group_id] = groups[row.group_id] or {}
            table.insert(groups[row.group_id], { chord = chord, flags = flags })
        end
        rows[row.action_id] = { chord = row.chord, flags = flags }
    end
    if not recovery then return nil end
    for _, members in pairs(groups) do
        if #members ~= 2 or members[1].chord ~= members[2].chord then return nil end
        if not (members[1].flags.release ~= members[2].flags.release) then return nil end
        if not (members[1].flags.locked == members[2].flags.locked) then return nil end
    end
    return rows
end

local function bundle_chord(chord)
    local normalized = normalized_chord(chord)
    if not normalized then return nil end
    local mods, key = {}, nil
    for raw in normalized:gmatch("[^+]+") do
        local part = raw:match("^%s*(.-)%s*$")
        if part == "SUPER" or part == "CTRL" or part == "ALT" or part == "SHIFT" then
            mods[part] = true
        else key = part end
    end
    local parts = {}
    for _, mod in ipairs({"SUPER", "CTRL", "ALT", "SHIFT"}) do
        if mods[mod] then table.insert(parts, mod) end
    end
    table.insert(parts, key or "")
    return table.concat(parts, " + ")
end

local function commit_bindings()
    local rows = validated_payload()
    if rows ~= nil then
        local seen = {}
        for _, item in ipairs(packaged) do
            if seen[item.action_id] or rows[item.action_id] == nil then rows = nil break end
            seen[item.action_id] = true
        end
        if rows ~= nil then
            for action_id in pairs(rows) do
                if not seen[action_id] and not optional_action_ids[action_id] then rows = nil break end
            end
        end
    end
    local occupied = {}
    for _, item in ipairs(packaged) do
        local selected = rows and rows[item.action_id] or item
        if selected.chord then
            hl.bind(selected.chord, item.implementation, selected.flags)
            occupied[bundle_chord(selected.chord)] = true
        end
    end
    local ok, bundles = pcall(require, "config.luminophore_bundles")
    if luminophoreCompositor and ok and type(bundles) == "table" then
        for _, bundle in ipairs(bundles) do
            local chord = bundle_chord(bundle.chord)
            if type(bundle.id) == "string" and bundle.id:match("^[a-z0-9-]+$") and chord and not occupied[chord] then
                hl.bind(bundle.chord, hl.dsp.exec_cmd(luminophoreShell .. " launch-bundle " .. bundle.id))
                occupied[chord] = true
            end
        end
    end
end

---------------------------
---- WINDOW MANAGEMENT ----
---------------------------

-- Window manipulation
bind("window.kill_active", mainMod .. " + Escape",      hl.dsp.exec_cmd(compositorControl .. " kill"))
bind("window.close", mainMod .. " + Q",           hl.dsp.window.close())
bind("window.float", mainMod .. " + D", hl.dsp.window.float({ action = "toggle" }))
bind("window.fullscreen", mainMod .. " + F",           occupyOutput)

-- Change focus
bind("window.cycle", "ALT + Tab",           hl.dsp.window.cycle_next())
bind("shell.launcher", mainMod .. " + Tab",   hl.dsp.exec_cmd(luminophoreCall .. "open launcher"))

-- Move & Resize with mouse
bind("window.drag.begin", mainMod .. " + mouse:272", hl.dsp.window.drag())
bind("window.resize", mainMod .. " + mouse:273", hl.dsp.window.resize())

-- Let expanded luminophore surfaces dismiss themselves on a click outside without
-- intercepting the click or spawning a helper process for every button press.
bind("shell.outside_click", "mouse:272", function()
    local cursor = hl.get_cursor_pos()
    if cursor ~= nil then
        hl.dispatch(hl.dsp.event("luminophore-shell-click:" .. cursor.x .. "," .. cursor.y))
    end
end, { release = true, non_consuming = true })

-- Zoom
local function zoomfunction(value)
    local zoomvalue = hl.get_config("cursor:zoom_factor")
    if (zoomvalue + value) > 3.0 then
        hl.config({ cursor = { zoom_factor = 3.0 } })
    elseif (zoomvalue + value) < 1.0 then
        hl.config({ cursor = { zoom_factor = 1.0 } })
    else
        hl.config({ cursor = { zoom_factor = zoomvalue + value } })
    end
end
bind("cursor.zoom_out", mainMod .. " + Minus", function() zoomfunction(-0.3) end, { repeating = true})
bind("cursor.zoom_in", mainMod .. " + Plus", function() zoomfunction(0.3) end, { repeating = true })

--# Zoom with keypad
bind("cursor.zoom_out_keypad", mainMod .. " + code:82", function() zoomfunction(-0.3) end, { repeating = true })
bind("cursor.zoom_in_keypad", mainMod .. " + code:86", function() zoomfunction(0.3) end, { repeating = true })


------------------
---- LAUNCHER ----
------------------

bind("app.terminal", mainMod .. " + Return",     launchApp(TERMINAL))
bind("app.files", mainMod .. " + E",          launchApp(FILE_MANAGER))
bind("app.editor", mainMod .. " + T",          launchApp(EDITOR))
bind("app.calculator", mainMod .. " + C",          launchApp(CALCULATOR))
bind("app.calculator_hardware", "XF86Calculator",           launchApp(CALCULATOR))
bind("app.browser", mainMod .. " + W",          launchApp(BROWSER))
bind("app.mission_center", "CONTROL + SHIFT + Escape", launchApp("missioncenter"))
bind("shell.settings", nil,          hl.dsp.exec_cmd(luminophoreCall .. "open system --provider settings"))
local desktopDispatcherOk, desktopDispatcher = pcall(function() return hl.dsp.luminophore.desktop_toggle() end)
if desktopDispatcherOk then
    bind("view.desktop.toggle", mainMod .. " + X", desktopDispatcher)
end
-- Open the full widget overview only after the standalone Super key is released. Opening
-- on key-down transfers layer-shell keyboard focus while Super is still held,
-- which can leave the search entry with an inconsistent modifier/focus state.
bind("shell.overview", mainMod .. " + SUPER_L",     hl.dsp.exec_cmd(luminophoreCall .. "toggle overview"), { release = true })
bind("shell.emoji", mainMod .. " + period",     hl.dsp.exec_cmd(luminophoreCall .. "open launcher --provider emoji"))

---------------------------
---- HARDWARE CONTROLS ----
---------------------------

-- Audio
bind("hardware.volume_up", "XF86AudioRaiseVolume", hl.dsp.exec_cmd(luminophoreCall .. "hardware volume-up"),   { locked = true, repeating = true })
bind("hardware.volume_down", "XF86AudioLowerVolume", hl.dsp.exec_cmd(luminophoreCall .. "hardware volume-down"), { locked = true, repeating = true })
bind("hardware.volume_mute", "XF86AudioMute",        hl.dsp.exec_cmd(luminophoreCall .. "hardware volume-mute"), { locked = true })
bind("hardware.microphone_mute", "XF86AudioMicMute",     hl.dsp.exec_cmd(luminophoreCall .. "hardware mic-mute"),    { locked = true })

-- Media
bind("hardware.media_toggle", "XF86AudioPlay",  hl.dsp.exec_cmd(luminophoreCall .. "hardware media-toggle"),   { locked = true })
bind("hardware.media_pause", "XF86AudioPause", hl.dsp.exec_cmd(luminophoreCall .. "hardware media-toggle"),   { locked = true })
bind("hardware.media_next", "XF86AudioNext",  hl.dsp.exec_cmd(luminophoreCall .. "hardware media-next"),     { locked = true })
bind("hardware.media_previous", "XF86AudioPrev",  hl.dsp.exec_cmd(luminophoreCall .. "hardware media-previous"), { locked = true })

-- Brightness
bind("hardware.brightness_up.preview", "XF86MonBrightnessUp",   hl.dsp.exec_cmd(luminophoreCall .. "hardware brightness-preview-up"),   { locked = true, repeating = true })
bind("hardware.brightness_down.preview", "XF86MonBrightnessDown", hl.dsp.exec_cmd(luminophoreCall .. "hardware brightness-preview-down"), { locked = true, repeating = true })
bind("hardware.brightness_up.commit", "XF86MonBrightnessUp",   hl.dsp.exec_cmd(luminophoreCall .. "hardware brightness-commit"), { locked = true, release = true })
bind("hardware.brightness_down.commit", "XF86MonBrightnessDown", hl.dsp.exec_cmd(luminophoreCall .. "hardware brightness-commit"), { locked = true, release = true })

-------------------
---- UTILITIES ----
-------------------

-- Screen Capture
bind("utility.color_picker", mainMod .. " + P",     hl.dsp.exec_cmd("hyprpicker -a -n"))
-- Screenshot: 영역 선택 → 클립보드만
bind("capture.region", "Print", hl.dsp.exec_cmd(captureRegion))

-- Screenshot: 영역 선택 → 사진 폴더 저장 + 클립보드
bind("capture.region_save", mainMod .. " + Print", hl.dsp.exec_cmd(captureRegionSave))

-- Theming and Wallpaper
bind("shell.appearance", mainMod .. " + SHIFT + W", hl.dsp.exec_cmd(luminophoreCall .. "open system --provider palette"))
bind("shell.wallpaper", mainMod .. " + ALT + W", hl.dsp.exec_cmd(luminophoreCall .. "wallpaper open"))

-- Clipboard
bind("shell.clipboard", mainMod .. " + V", hl.dsp.exec_cmd(luminophoreCall .. "open launcher --provider clip"))

-- Notifications
bind("shell.notifications", mainMod .. " + A", hl.dsp.exec_cmd(luminophoreCall .. "open notifications"))

-------------------------------
--------- WORKSPACES ----------
-------------------------------

bind("shell.spatial_editor", mainMod .. " + SPACE", hl.dsp.exec_cmd(luminophoreCall .. "toggle spatial-editor"))


-- Directional focus is available only in generations implementing this action.
local focusDispatchersOk, focusDispatchers = pcall(function()
    return {
        left = hl.dsp.luminophore.focus_direction({ direction = "left" }),
        right = hl.dsp.luminophore.focus_direction({ direction = "right" }),
        up = hl.dsp.luminophore.focus_direction({ direction = "up" }),
        down = hl.dsp.luminophore.focus_direction({ direction = "down" }),
    }
end)
if focusDispatchersOk then
    bind("window.focus.left", mainMod .. " + ALT + Left", focusDispatchers.left)
    bind("window.focus.right", mainMod .. " + ALT + Right", focusDispatchers.right)
    bind("window.focus.up", mainMod .. " + ALT + Up", focusDispatchers.up)
    bind("window.focus.down", mainMod .. " + ALT + Down", focusDispatchers.down)
end

-- Older compositors retain their supported bindings until the new binary is installed.
local historyOk, history = pcall(function()
    return { undo = hl.dsp.luminophore.undo(), redo = hl.dsp.luminophore.redo() }
end)
if historyOk then
    bind("spatial.undo", mainMod .. " + Z", history.undo)
    bind("spatial.redo", mainMod .. " + SHIFT + Z", history.redo)
end

-- Keep every dispatcher introduced by the new spatial compositor in one final,
-- atomic optional block.  Resolve all implementations before appending any of
-- them: an older live binary must retain the complete established binding set
-- and must never receive a partial spatial binding set.
local spatialDispatchersOk, spatialDispatchers = pcall(function()
    return {
        viewMoveLeft = hl.dsp.luminophore.view_move({ direction = "left" }),
        viewMoveRight = hl.dsp.luminophore.view_move({ direction = "right" }),
        viewMoveUp = hl.dsp.luminophore.view_move({ direction = "up" }),
        viewMoveDown = hl.dsp.luminophore.view_move({ direction = "down" }),
        viewAdjustLeft = hl.dsp.luminophore.view_adjust({ direction = "left" }),
        viewAdjustRight = hl.dsp.luminophore.view_adjust({ direction = "right" }),
        viewAdjustUp = hl.dsp.luminophore.view_adjust({ direction = "up" }),
        viewAdjustDown = hl.dsp.luminophore.view_adjust({ direction = "down" }),
        boardMoveLeft = hl.dsp.luminophore.board_move({ direction = "left" }),
        boardMoveRight = hl.dsp.luminophore.board_move({ direction = "right" }),
        boardMoveUp = hl.dsp.luminophore.board_move({ direction = "up" }),
        boardMoveDown = hl.dsp.luminophore.board_move({ direction = "down" }),
        wideToggle = hl.dsp.luminophore.wide_toggle(),
    }
end)
if spatialDispatchersOk then
    bind("view.move.left", mainMod .. " + Left", spatialDispatchers.viewMoveLeft)
    bind("view.move.right", mainMod .. " + Right", spatialDispatchers.viewMoveRight)
    bind("view.move.up", mainMod .. " + Up", spatialDispatchers.viewMoveUp)
    bind("view.move.down", mainMod .. " + Down", spatialDispatchers.viewMoveDown)
    bind("view.adjust.left", mainMod .. " + CTRL + Left", spatialDispatchers.viewAdjustLeft)
    bind("view.adjust.right", mainMod .. " + CTRL + Right", spatialDispatchers.viewAdjustRight)
    bind("view.adjust.up", mainMod .. " + CTRL + Up", spatialDispatchers.viewAdjustUp)
    bind("view.adjust.down", mainMod .. " + CTRL + Down", spatialDispatchers.viewAdjustDown)
    bind("board.move.left", mainMod .. " + SHIFT + Left", spatialDispatchers.boardMoveLeft)
    bind("board.move.right", mainMod .. " + SHIFT + Right", spatialDispatchers.boardMoveRight)
    bind("board.move.up", mainMod .. " + SHIFT + Up", spatialDispatchers.boardMoveUp)
    bind("board.move.down", mainMod .. " + SHIFT + Down", spatialDispatchers.boardMoveDown)
    bind("view.wide.toggle", mainMod .. " + G", spatialDispatchers.wideToggle)
end

commit_bindings()
