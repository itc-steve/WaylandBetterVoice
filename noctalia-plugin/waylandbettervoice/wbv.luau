local M = {}
M.panelId = "itc-steve/waylandbettervoice:panel"
M.indicatorId = "itc-steve/waylandbettervoice:indicator"
local modes = {idle = true, listening = true, dictating = true, transcribing = true, meeting = true, error = true}
local colors = {listening = "tertiary", dictating = "primary", transcribing = "secondary", meeting = "error", error = "error"}

local function number(value)
    if type(value) ~= "number" or value ~= value or math.abs(value) == math.huge then return 0 end
    return math.max(0, value)
end

function M.normalize(data)
    if type(data) ~= "table" or not modes[data.mode] then return nil end
    local meeting = type(data.meeting) == "table" and data.meeting or {}
    -- Only presentation fields cross VMs; never publish transcripts or meeting paths.
    return {
        mode = data.mode, level = math.min(1, number(data.level)),
        model = type(data.model) == "string" and data.model or "",
        model_loaded = data.model_loaded == true,
        inject_method = type(data.inject_method) == "string" and data.inject_method or "wtype",
        elapsed = number(meeting.elapsed),
        error = type(data.error) == "string" and string.sub(data.error, 1, 240) or "",
    }
end

function M.status()
    return noctalia.state.get("status") or {mode = "offline", level = 0, elapsed = 0, error = ""}
end

function M.color(status)
    return colors[status.mode] or "on_surface"
end

function M.active(status)
    return status.mode == "listening" or status.mode == "dictating"
        or status.mode == "transcribing" or status.mode == "meeting"
end

function M.elapsed(seconds)
    local n = math.floor(number(seconds))
    return string.format("%02d:%02d", math.floor(n / 60), n % 60)
end

function M.busy()
    local deadline = noctalia.state.get("busy")
    if type(deadline) == "number" and deadline > os.time() then return true end
    if deadline then noctalia.state.set("busy", false) end
    return false
end

function M.command(command, action)
    local allowed = {dictate = {toggle = true, cancel = true}, meeting = {toggle = true},
        model = {load = true, unload = true}, inject = {wtype = true, ydotool = true, clipboard = true}}
    if not allowed[command] or not allowed[command][action] then return end
    if M.busy() then return end
    -- CLI timeout is at most 180s; lease recovers callbacks lost during entry reload.
    noctalia.state.set("busy", os.time() + 185)
    noctalia.state.set("command_error", "")
    local function finish(result)
        noctalia.state.set("busy", false)
        if result.exitCode ~= 0 or result.timedOut then
            local error = result.timedOut and "Command timed out; check status before retrying."
                or "wbv " .. command .. " " .. action .. " failed. Check the daemon and wbv CLI."
            noctalia.state.set("command_error", error)
            noctalia.notifyError("WaylandBetterVoice", error)
        end
        -- Command stdout may contain dictated text; do not display, store, or log it.
    end
    if not noctalia.runAsync({"wbv", command, action}, finish) then
        finish({exitCode = 1})
    end
end

function M.indicator(status, size, phase)
    if status.mode == "meeting" then
        return ui.row({gap = 6, align = "center"}, {
            ui.box({width = 6, height = 6, radius = 3, fill = "error/0.7"}),
            ui.label({text = "Meeting " .. M.elapsed(status.elapsed), fontSize = size}),
        })
    end
    return ui.row({gap = 6, align = "center"}, {
        ui.box({width = size, height = size, radius = size / 2, fill = M.color(status),
            opacity = math.min(1, 0.4 + math.min(0.4, (status.level or 0) * 6)
                + (phase and 0.2 * (1 + math.sin(phase)) / 2 or 0))}),
        ui.label({text = status.mode, fontSize = size}),
    })
end
return M
