-- Run from repository root: lua noctalia-plugin/test_plugin.lua
-- Host mocks only: never executes wbv or reads user state.
local root = 'noctalia-plugin/waylandbettervoice/'
local values, queued, notices = {}, {}, {}
local fixture
local accept = true
local showIndicator = true
noctalia = {
    state = { get = function(k) return values[k] end,
        set = function(k, v) values[k] = v end,
        watch = function() end },
    json = { decode = function() return fixture end },
    getConfig = function(key)
        assert(key == 'show_indicator')
        return showIndicator
    end,
    setUpdateInterval = function() end,
    runAsync = function(argv, cb)
        if not accept then return false end
        table.insert(queued, {argv = argv, cb = cb})
        return true
    end,
    notifyError = function(_, body) table.insert(notices, body) end,
    togglePanel = function(id) values.panel = id end,
}
local nativeRequire = require
require = function(path)
    if path == './wbv.luau' then return dofile(root .. 'wbv.luau') end
    return nativeRequire(path)
end
local wbv = require('./wbv.luau')
assert(wbv.normalize(nil) == nil)
assert(wbv.normalize({mode = 'bogus'}) == nil)
local s = wbv.normalize({mode = 'meeting', meeting = {active = true, elapsed = 125},
    level = 5, last_text = 'PRIVATE FIXTURE', model_loaded = true})
assert(s.mode == 'meeting' and s.level == 1 and s.elapsed == 125)
assert(s.last_text == nil and s.meeting == nil)
assert(wbv.elapsed(125) == '02:05' and wbv.elapsed(-1) == '00:00')
assert(wbv.normalize({mode = 'idle', meeting = 'bad', level = 'bad'}).elapsed == 0)
assert(wbv.normalize({mode = 'idle', level = math.huge}).level == 0)
values.busy = os.time() - 1
assert(not wbv.busy() and not values.busy) -- reload lease recovery
wbv.command('dictate', 'toggle')
assert(values.busy and queued[#queued].argv[1] == 'wbv')
local count = #queued
wbv.command('meeting', 'toggle')
assert(#queued == count) -- shared busy gate
queued[#queued].cb({exitCode = 1, stderr = 'failed'})
assert(not values.busy and values.command_error ~= '')
accept = false
wbv.command('inject', 'ydotool')
assert(not values.busy and #notices > 0)
accept = true
wbv.command('model', 'download')
assert(#queued == count) -- no implicit downloads or arbitrary actions
for _, args in ipairs({{'dictate', 'toggle'}, {'dictate', 'cancel'}, {'meeting', 'toggle'},
    {'model', 'load'}, {'model', 'unload'}, {'inject', 'wtype'}, {'inject', 'ydotool'}, {'inject', 'clipboard'}}) do
    wbv.command(args[1], args[2])
    local command = queued[#queued]
    assert(command.argv[1] == 'wbv' and command.argv[2] == args[1] and command.argv[3] == args[2])
    command.cb({exitCode = 0, stdout = 'PRIVATE FIXTURE'})
    assert(not values.busy and values.command_error == '')
end

dofile(root .. 'service.luau')
update()
local poll = queued[#queued]
assert(poll.argv[2] == 'status' and poll.argv[3] == '--json')
count = #queued
update()
assert(#queued == count) -- no overlapping status requests
fixture = {mode = 'listening', level = 0.1, model_loaded = true}
poll.cb({exitCode = 0, stdout = '{}'})
assert(values.status.mode == 'listening')
assert(queued[#queued].argv[3] == 'panel-open')
queued[#queued].cb({exitCode = 0})
update()
queued[#queued].cb({exitCode = 1})
assert(values.status.mode == 'offline' and queued[#queued].argv[3] == 'panel-close')
queued[#queued].cb({exitCode = 1})
assert(values.indicator_error ~= '')
update()
fixture = {mode = 'invalid'}
queued[#queued].cb({exitCode = 0, stdout = '{}'})
assert(values.status.mode == 'offline')
assert(queued[#queued].argv[3] == 'panel-close') -- retry failed indicator IPC
queued[#queued].cb({exitCode = 0})
assert(values.indicator_error == '')
update()
fixture = {mode = 'idle'}
queued[#queued].cb({exitCode = 0, stdout = '{}', stdoutTruncated = true})
assert(values.status.mode == 'offline')
update()
queued[#queued].cb({exitCode = 0, stdout = '{}'})
assert(values.status.mode == 'idle') -- recovery without reload

ui = setmetatable({}, {__index = function(_, kind)
    return function(props, children) return {kind = kind, props = props, children = children} end
end})
local tree
panel = {render = function(t) tree = t end, close = function() end,
    setWantsSecondTicks = function() end, setNeedsFrameTick = function() end}
barWidget = {render = function(t) tree = t end}
dofile(root .. 'widget.luau')
update()
assert(tree.kind == 'row')
onRightClick()
assert(values.panel == 'itc-steve/waylandbettervoice:panel')
dofile(root .. 'panel.luau')
onOpen()
assert(tree.kind == 'column')
values.command_error = ''
values.status = {mode = 'error', error = 'Fixture error'}
update()
assert(tree.children[6].props.text == 'Fixture error')
local renders, closes, frameRequests = 0, 0, 0
local frameTicks, secondTicks = false, false
panel = {
    render = function(t) tree = t; renders = renders + 1 end,
    close = function()
        closes = closes + 1
        frameTicks, secondTicks = false, false -- host stops ticks on close
        onClose()
    end,
    setWantsSecondTicks = function(enabled) secondTicks = enabled end,
    setNeedsFrameTick = function(enabled)
        frameRequests = frameRequests + 1
        frameTicks = enabled
    end,
}
values.status = {mode = 'listening', level = 0.1}
dofile(root .. 'indicator.luau')
onOpen()
assert(closes == 0 and renders == 1 and tree.kind == 'row')
assert(frameTicks and secondTicks)
onFrameTick(16)
assert(renders == 2 and frameRequests == 2)

showIndicator = false
update() -- active recording must close locally when disabled
assert(closes == 1 and renders == 2 and frameRequests == 2)
assert(not frameTicks and not secondTicks)
update()
onFrameTick(16) -- even a late queued callback must not render or request ticks
assert(closes == 1 and renders == 2 and frameRequests == 2)

-- Config edits rebuild panel runtimes; disabled onOpen must close before rendering.
dofile(root .. 'indicator.luau')
onOpen()
assert(closes == 2 and renders == 2 and frameRequests == 2)
assert(not frameTicks and not secondTicks)
showIndicator = true
onOpen()
assert(closes == 2 and renders == 3 and frameTicks and secondTicks)
values.status = s -- meeting stays visible without frame animation
update()
assert(renders == 4 and not frameTicks and secondTicks)
values.status = {mode = 'idle'}
update()
assert(closes == 3 and renders == 4 and not frameTicks and not secondTicks)
update()
onFrameTick(16)
assert(closes == 3 and renders == 4 and frameRequests == 4)
print('plugin self-check: passed (normalization, privacy, commands, failures, polling, recovery, UI, indicator visibility)')
