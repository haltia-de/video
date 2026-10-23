-- SPDX-License-Identifier: GPL-2.0
-- Resolve each entry when it opens: private CDN links expire, and the
-- account token belongs to Video, never to this player process.
local mp = require 'mp'
local pending, serial = nil, 0
local ready = mp.add_periodic_timer(0.1, function()
    mp.commandv('script-message', 'haltia-queue-ready')
end)

mp.add_hook('on_load', 5, function(hook)
    local index = mp.get_property('stream-open-filename', ''):match('^haltia%-queue://(%d+)$')
    if not index then return end
    ready:kill()
    hook:defer()
    serial = serial + 1
    local request = serial
    pending = hook
    mp.commandv('script-message', 'haltia-queue-load', tostring(request), index)
    mp.add_timeout(30, function()
        if pending == hook then
            pending = nil
            hook:cont()
            mp.commandv('quit')
        end
    end)
end)

mp.register_script_message('haltia-queue-resolved', function(request, url, ua, title, start)
    if tonumber(request) ~= serial or not pending then return end
    mp.set_property('stream-open-filename', url)
    mp.set_property('file-local-options/ytdl', 'no')
    mp.set_property('file-local-options/user-agent', ua)
    mp.set_property('file-local-options/force-media-title', title)
    mp.set_property('file-local-options/start', start)
    local hook = pending
    pending = nil
    hook:cont()
end)

mp.register_event('end-file', function(event)
    if event.reason == 'error' then
        mp.commandv('quit') -- do not skip through a private playlist after a load failure
    end
end)
