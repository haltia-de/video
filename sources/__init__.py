# SPDX-License-Identifier: GPL-2.0
"""The places videos come from. Every module of this package that defines
SOURCE is one; b8k-video lists them all and needs nothing else to know
about a new one (Vimeo, ...).

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
and may have:
  warm_up()                 called once in the background at start
  owns(text)                true when the text is a link to this source (a
                            channel link pasted to search finds the
                            channel there, whichever source is chosen)
  home()                    {"title": ..., "videos": [...]} for Главная
                            (what is popular, or picked for the account),
                            or None when there is nothing to show
  ytdl_options()            {name: value} yt-dlp options the player needs
                            for this source's videos (cookies)

and, to sign in, an account:
  login                     "code" (a link or QR code confirmed on a
                            phone) or "cookies" (a file the person puts
                            in place; login_hint tells where)
  account()                 {"name": ...} when signed in, else None; cheap,
                            from what is stored
  login_start()             for "code": {"url": ..., "code": ...}
  login_poll()              for "code": None while waiting, the account
                            once confirmed; raises when the code expired
  login_check()             for "cookies": the account, or None
  logout()
  subscriptions()           the account's channels, to follow here too

A video is a dict:
  source, id, title, channel, channel_id, duration (seconds or None),
  thumb (an image URL), published (a Unix time or None)
A channel is a dict:
  source, id, title
and, when the source knows them (a followed channel may lack them until
b8k-video asks find_channel again):
  handle (like "@name"), thumb (an avatar URL), subscribers (a count or
  None), description

A source may block (network): b8k-video calls it from worker threads, more
than one at a time.
"""
import importlib
import json
import os
import pkgutil
import sys

# Where the sources keep what they must (an account, a token).
CONFIG_DIR = os.path.expanduser("~/.config/b8k")
CACHE_DIR = os.path.expanduser("~/.cache/b8k-video")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data, private=False):
    """Written whole or not at all; private: readable by the owner only."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


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
