local wbv = require("./wbv.luau")
local opened = false
local phase = 0

function update()
    if not opened then return end
    local status = wbv.status()
    if not noctalia.getConfig("show_indicator") or not wbv.active(status) then panel.close(); return end
    panel.setNeedsFrameTick(status.mode ~= "meeting")
    panel.render(wbv.indicator(status, 16, phase))
end

function onOpen() opened = true; panel.setWantsSecondTicks(true); update() end
function onClose() opened = false end
function onFrameTick(deltaMs) phase = (phase + deltaMs / 800) % (2 * math.pi); update() end
noctalia.state.watch("status", update)
