-- Persistent diagnostics for `kilix video`. Explicitly loaded with user scripts
-- disabled. Keep this separate from timed OSD messages and the seek controls.
local mp = require 'mp'
local messages = {}
local seen = {}
local extra = 0
local overlay = mp.create_osd_overlay('ass-events')
overlay.z = 1000
local utf8_char = '[%z\1-\127\194-\244][\128-\191]*'

local function clean(text)
    return text:gsub('\27%][^\7]*\7', '')
               :gsub('\27%[[0-?]*[ -/]*[@-~]', '')
               :gsub('[%z\1-\31\127]', ' ')
               :gsub('%s+', ' '):match('^%s*(.-)%s*$')
end

local function wrap(text, columns)
    -- Split on UTF-8 character boundaries; bound the panel to two rows per item.
    local chars = {}
    for c in text:gmatch(utf8_char) do
        chars[#chars + 1] = c
    end
    local lines = {}
    for start = 1, math.min(#chars, columns * 2), columns do
        lines[#lines + 1] = table.concat(chars, '', start,
                                       math.min(start + columns - 1, #chars))
    end
    if #chars > columns * 2 then
        lines[#lines] = lines[#lines] .. '…'
    end
    return lines
end

local function draw()
    if #messages == 0 then return end
    local width, height = mp.get_osd_size()
    if width <= 0 or height <= 0 then return end
    local resx = 720 * width / height
    local columns = math.max(12, math.floor((resx - 36) / 11))
    local lines = {'Playback warnings / errors (retained until exit)'}
    for _, text in ipairs(messages) do
        for _, line in ipairs(wrap(text, columns)) do
            lines[#lines + 1] = line
        end
    end
    lines[#lines + 1] = extra > 0
        and 'Additional messages received; details after playback.'
        or 'Details also printed after playback.'
    local box_height = #lines * 24 + 16
    local text = {}
    for _, line in ipairs(lines) do
        text[#text + 1] = mp.command_native({'escape-ass', line})
    end
    overlay.res_x, overlay.res_y = resx, 720
    overlay.data = string.format(
        '{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H101010&\\1a&H00&\\p1}' ..
        'm 6 6 l %g 6 %g %g 6 %g{\\p0}\n' ..
        '{\\an7\\pos(14,12)\\fnmonospace\\fs18\\bord0\\shad0\\1c&HFFFFFF&\\q2}%s',
        resx - 6, resx - 6, box_height, box_height, table.concat(text, '\\N'))
    overlay:update()
end

local redraw = mp.add_timeout(0.05, draw)
redraw:kill()
local function schedule_draw()
    if not redraw:is_enabled() then redraw:resume() end
end

mp.register_event('log-message', function(event)
    if event.level ~= 'warn' and event.level ~= 'error' and event.level ~= 'fatal' then
        return
    end
    -- terminal_video.py enables this on WSL on purpose; mpv warns every run.
    if event.text:find('The --audio-stream-silence option is set.', 1, true) then
        return
    end
    local text = clean('[' .. event.prefix .. '] ' .. event.text)
    -- Bound memory and suppress repeated decoder complaints. Pin the first four
    -- distinct messages: later errors must not erase the original explanation.
    if #text > 2048 then
        local chars, bytes = {}, 0
        for c in text:gmatch(utf8_char) do
            if bytes + #c > 2045 then break end
            chars[#chars + 1], bytes = c, bytes + #c
        end
        text = table.concat(chars) .. '…'
    end
    if seen[text] then return end
    if #messages < 4 then
        messages[#messages + 1] = text
    else
        extra = extra + 1
    end
    if extra < 128 then seen[text] = true end
    -- stderr is collected by the supervisor and replayed after tty restoration.
    io.stderr:write(text, '\n')
    io.stderr:flush()
    schedule_draw()
end)
mp.enable_messages('warn')
-- Once subscribed, route diagnostics through the panel and supervisor. Raw log
-- lines would otherwise cover the graphics and leave duplicate text on pause.
if mp.get_property('options/vo', '') == 'kitty' then
    mp.set_property('msg-level', 'all=no,terminal_video_errors=warn')
    mp.register_event('shutdown', function()
        mp.set_property('msg-level', 'all=warn')
    end)
end
mp.observe_property('osd-dimensions', 'native', schedule_draw)
