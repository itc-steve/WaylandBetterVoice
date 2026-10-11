local wbv = require("./wbv.luau")
local opened = false

function update()
    if not opened then return end
    local status = wbv.status()
    local busy = wbv.busy()
    local ready = status.mode ~= "offline" and not busy
    local error = noctalia.state.get("command_error")
    if not error or error == "" then error = status.error or "" end
    if error == "" then error = noctalia.state.get("indicator_error") or "" end
    local function button(text, command, action)
        return ui.button({text = text, enabled = ready,
            onClick = function()
                if command == "dictate" and action == "toggle" then panel.close() end
                wbv.command(command, action)
                update()
            end})
    end
    panel.render(ui.column({gap = 10, padding = 12}, {
        ui.label({text = "WaylandBetterVoice", fontWeight = "bold", fontSize = 18}),
        wbv.indicator(status, 14),
        ui.label({text = (status.model or "") .. (status.model_loaded and " · loaded" or " · not loaded")}),
        ui.label({text = "Typing: " .. (status.inject_method or "wtype")}),
        ui.label({text = busy and "Command in progress…" or "", maxLines = 2}),
        ui.label({text = error, color = "error", maxLines = 3}),
        ui.row({gap = 8}, {button("Dictation toggle", "dictate", "toggle"), button("Cancel utterance", "dictate", "cancel")}),
        ui.row({gap = 8}, {button("Meeting toggle", "meeting", "toggle"), button("Load model", "model", "load"), button("Unload", "model", "unload")}),
        ui.label({text = "Typing method (saved immediately):"}),
        ui.row({gap = 8}, {button("Native · wtype", "inject", "wtype"), button("Electron · ydotool", "inject", "ydotool"), button("Clipboard", "inject", "clipboard")}),
        ui.label({text = "Default niri binds: Super+Space · dictation; Super+Alt+Space · meeting.", maxLines = 2}),
        ui.button({text = "Close", onClick = function() panel.close() end}),
    }))
end

function onOpen() opened = true; panel.setWantsSecondTicks(true); update() end
function onClose() opened = false end
noctalia.state.watch("status", update)
noctalia.state.watch("busy", update)
noctalia.state.watch("command_error", update)
noctalia.state.watch("indicator_error", update)
