local wbv = require("./wbv.luau")
noctalia.setUpdateInterval(1000)

function update()
    local status = wbv.status()
    barWidget.render(ui.row({gap = 6, align = "center"}, {
        ui.glyph({name = "microphone", color = wbv.color(status)}),
        wbv.indicator(status, 12),
    }))
end
noctalia.state.watch("status", update)

function onClick() wbv.command("dictate", "toggle") end
function onRightClick() noctalia.togglePanel(wbv.panelId) end
function onMiddleClick() noctalia.togglePanel(wbv.panelId) end
