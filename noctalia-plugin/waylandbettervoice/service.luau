local wbv = require("./wbv.luau")
local polling = false
local indicatorOpen = nil
local indicatorPending = false
noctalia.setUpdateInterval(1000)

local function publish(status)
    noctalia.state.set("status", status)
    local show = noctalia.getConfig("show_indicator") and wbv.active(status)
    if show ~= indicatorOpen and not indicatorPending then
        -- Only togglePanel is exposed in-process; IPC open/close are idempotent on reload.
        indicatorPending = true
        local function done(result)
            indicatorPending = false
            if result.exitCode == 0 and not result.timedOut then
                indicatorOpen = show
                noctalia.state.set("indicator_error", "")
            else
                noctalia.state.set("indicator_error", "Recording indicator unavailable; check Noctalia plugin entries.")
            end
        end
        if not noctalia.runAsync({"noctalia", "msg", show and "panel-open" or "panel-close", wbv.indicatorId}, done) then
            done({exitCode = 1})
        end
    end
end

function update()
    if polling then return end
    polling = true
    local function finish(result)
        polling = false
        local status
        if result.exitCode == 0 and not result.timedOut and not result.stdoutTruncated then
            local ok, data = pcall(noctalia.json.decode, result.stdout)
            if ok then status = wbv.normalize(data) end
        end
        publish(status or {mode = "offline", level = 0, elapsed = 0,
            error = "Daemon unreachable or invalid status. Check wbv status --json."})
    end
    if not noctalia.runAsync({"wbv", "status", "--json"}, finish) then finish({exitCode = 1}) end
end

function onExit(_signal, reason)
    if reason == "disable" or reason == "uninstall" then
        noctalia.runAsync({"noctalia", "msg", "panel-close", wbv.indicatorId})
    end
end
