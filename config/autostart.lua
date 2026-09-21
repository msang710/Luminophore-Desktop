-- Auto-start config
-- if you dont use UWSM add your auto start programs here, otherwise use XDG autostart https://wiki.archlinux.org/title/XDG_Autostart

hl.on("hyprland.start", function ()
    -- Only the owning Luminophore session runner can activate its services.
    local ready = os.getenv("LUMINOPHORE_SESSION_READY_COMMAND")
    if ready and ready ~= "" then
        hl.exec_cmd(ready)
    elseif os.getenv("LUMINOPHORE_RELEASE_ROOT") then
        -- Development canaries retain their pinned, directly supervised shell.
        local releaseShell = os.getenv("LUMINOPHORE_SHELL_COMMAND")
        assert(releaseShell and releaseShell ~= "", "release session is missing its pinned shell command")
        hl.exec_cmd(releaseShell)
    end
    -- A committed native scene transfers wallpaper ownership permanently.
    -- Before that first explicit activation, preserve the existing session setup.
    local stateRoot = os.getenv("XDG_STATE_HOME") or (os.getenv("HOME") .. "/.local/state")
    local nativeState = io.open(stateRoot .. "/luminophore-shell/background-state.json", "r")
    if nativeState then
        nativeState:close() -- Shell restores its native last-good scene.
    elseif os.getenv("LUMINOPHORE_COMPOSITOR") ~= "1" then
        hl.exec_cmd("hyprpaper")
    end
    if os.getenv("LUMINOPHORE_COMPOSITOR") ~= "1" then
        hl.exec_cmd(os.getenv("HOME") .. "/.config/hypr/config/scripts/load-window-glow-plugin")
    end
end)
