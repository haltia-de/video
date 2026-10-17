# SPDX-License-Identifier: GPL-2.0
"""The places videos come from. Every module of this package that defines
SOURCE is one; b8k-video lists them all and needs nothing else to know
about a new one (VK Video, Vimeo, ...).

SOURCE has:
  id, label                 "youtube", "YouTube"
  search(query)             a list of videos, best first
  channel_videos(channel)   the channel's newest videos, newest first
  search_channels(query)    channels matching the words, best first, or
                            the one a link or @name points at
  find_channel(text)        a channel from a link, a name or its id, with
                            everything known about it, or None
  watch_url(video)          what the player opens; it goes through mpv's
                            yt-dlp hook, so a page link is enough
and may have warm_up(), called once in the background at start, and
owns(text), true when the text is a link to this source (a channel link
pasted to search finds the channel there, whichever source is chosen).

A video is a dict:
  source, id, title, channel, channel_id, duration (seconds or None),
  thumb (an image URL), published (a Unix time or None)
A channel is a dict:
  source, id, title
and, when the source knows them (a followed channel may lack them until
b8k-video asks find_channel again):
  handle (like "@name"), thumb (an avatar URL), subscribers (a count or
  None), description

A source may block (network): b8k-video calls it from a worker thread.
"""
import importlib
import pkgutil
import sys


def load():
    """{id: SOURCE} of every source that imports; a broken one is skipped."""
    found = {}
    for info in pkgutil.iter_modules(__path__):
        try:
            mod = importlib.import_module("%s.%s" % (__name__, info.name))
        except Exception as exc:  # a source must never take the program down
            sys.stderr.write("b8k-video: source %s: %s\n" % (info.name, exc))
            continue
        src = getattr(mod, "SOURCE", None)
        if src is not None:
            found[src.id] = src
    return found
