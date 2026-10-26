# SPDX-License-Identifier: GPL-2.0
"""The places videos come from. Every module of this package that defines
create_source() (or a legacy SOURCE) is one; Видео lists them all and needs nothing else to know
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
  resolve_bookmark(url)      (kind, object), kind video/playlist/channel;
                            only accepts this Source's links
  bookmark_client()         captured Account session or None; id, name,
                            capabilities() -> supported kinds (read-only
                            probe), read(kinds) -> complete [(kind, object)],
                            write(kind, object, add). Must never fall back
                            to a guest or a replacement Account's token
  playlists(channel)        a list of playlists: source, owner_id (channel
                            id), id, title; optional count of videos
  playlist_videos(playlist, offset=0)
                            {"videos": [...], "next": offset or None};
                            keeps playlist order and actual video channels
  warm_up()                 called once in the background at start
  owns(text)                true when the text is a link to this source (a
                            channel link pasted to search finds the
                            channel there, whichever source is chosen)
  home()                    {"title": ..., "videos": [...]} for Главная
                            (what is popular, or picked for the account),
                            or None when there is nothing to show
  ytdl_options()            {name: value} yt-dlp options the player needs
                            for this source's videos (cookies)
  stream(video, fmt)        {"urls": [video, audio] or [both], "user_agent"}
                            for a format: the player then needs no yt-dlp
                            of its own (haltia-vplay; a new yt-dlp per
                            video is slow on the tablet)
  browser_stream(video, fmt) private descriptor of HTTP tracks, headers,
                            containers and codecs for the web media adapter;
                            returned by browser_stream_of(), never sent to UI
  resolve_on_desktop        true when stream() must resolve for mpv too
                            (a signed-in VK account cannot be passed to
                            the player's guest yt-dlp); defaults to false

and, to sign in, an account:
  login                     "code" (a link or QR code confirmed on a
                            phone) or "cookies" (a file the person puts
                            in place; login_hint tells where)
  account()                 {"name": ...} when signed in, else None; cheap,
                            from what is stored
  login_start()             for "code": {"url": ..., "code": ...}
  login_poll()              for "code": None while waiting, the account
                            once confirmed, {"need_code": True} when the
                            phone shows a code; raises when the QR expired
  login_submit(code)        for "code" with need_code: "accepted" (poll
                            for the account), "incorrect" or "expired"
  login_check(browser=None) for "cookies": the account, or None; with a
                            browser (an id from browsers()), from its
                            cookies instead of the file
  browsers()                for "cookies": [(id, label)] of the browsers
                            it can sign in through here, maybe none
  logout()
  subscriptions()           the account's channels, to follow here too

A video is a dict:
  source, id, title, channel, channel_id, duration (seconds or None),
  thumb (an image URL), published (a Unix time or None); short: True for
  a Short (YouTube's Shorts, VK's clips), shown only when the person asks
A channel is a dict:
  source, id, title
and, when the source knows them (a followed channel may lack them until
Видео asks find_channel again):
  handle (like "@name"), thumb (an avatar URL), subscribers (a count or
  None), description

What an account keeps secret goes into vault_in(config_dir): haltia-kit's
Vault (the keyring, else a file). When the person refuses to unlock the
keyring, such a source sets locked = True, answers as signed out (and
plays as a guest) without dropping anything, and asks again only on
unlock(), which may block (the desktop's prompt) and leaves locked set
when refused once more.

A source may block (network): Видео calls it from worker threads, more
than one at a time.
"""
import importlib
import os
import pkgutil
import sys

from haltia_video.adapters.storage.json_store import load_json, save_json
from haltia_video.adapters.platform.paths import defaults



def xdg(var, default):
    """A base directory of the XDG spec ($XDG_CONFIG_HOME, ...): Flatpak
    gives each program its own."""
    return os.environ.get(var) or os.path.expanduser(default)


APP_ID = "io.github.haltia_de.Video"


def vault_in(config_dir, **kwargs):
    """The Vault of the Accounts' secrets: kept under Видео's app id, with
    config_dir/<name> as the file where no keyring answers (and where
    2.0's files are found); kwargs as haltia_kit.vault.Vault's (keyring=
    None in the tests)."""
    from haltia_kit.vault import Vault

    return Vault(APP_ID, config_dir, label="Видео", **kwargs)


# Where Видео keeps what it must: the Library's files, an account, a token.
CONFIG_DIR, DATA_DIR, CACHE_DIR = defaults()


def stream_of(info):
    """What a source's stream() answers, from yt-dlp's info of one video
    asked for in a format."""
    formats = info.get("requested_formats") or [info]
    urls = [f.get("url") for f in formats]
    # Only the web's protocols, as mpv's yt-dlp hook allows: the player is
    # given these as they are, and edl://, file: or av: would make it open
    # whatever the answer names (it then tries the page through the hook).
    if not urls or not all(isinstance(u, str) and u.startswith(("https://", "http://"))
                           for u in urls):
        raise ValueError("yt-dlp gave no web stream for %s" % info.get("id"))
    return {"urls": urls,
            "user_agent": (formats[0].get("http_headers") or {}).get("User-Agent")}


def browser_stream_of(info):
    """Private server-side descriptor; never returned as browser JSON.
    Keep the extraction metadata needed to select delivery and codecs.
    """
    stream_of(info)  # the same URL/protocol validation as the local player
    formats = info.get("requested_formats") or [info]
    tracks = []
    for fmt in formats:
        video = fmt.get("vcodec") not in (None, "none")
        audio = fmt.get("acodec") not in (None, "none")
        headers = dict(info.get("http_headers") or {})
        headers.update(fmt.get("http_headers") or {})
        tracks.append({"url": fmt["url"], "headers": headers,
                       "kind": "combined" if video and audio else "audio" if audio else "video",
                       "container": fmt.get("ext") or info.get("ext"),
                       "vcodec": fmt.get("vcodec"), "acodec": fmt.get("acodec"),
                       "height": fmt.get("height"), "protocol": fmt.get("protocol")})
    return {"tracks": tracks, "duration": info.get("duration"),
            "height": max((f.get("height") or 0 for f in formats), default=0)}


def load():
    """Fresh source instances owned by the application, not import globals."""
    found = {}
    for info in pkgutil.iter_modules(__path__):
        try:
            mod = importlib.import_module("%s.%s" % (__name__, info.name))
            factory = getattr(mod, "create_source", None)
            src = factory() if factory else getattr(mod, "SOURCE", None)
        except Exception as exc:  # a source must never take the program down
            sys.stderr.write("haltia-video: source %s: %s\n" % (info.name, exc))
            continue
        if src is not None:
            found[src.id] = src
    return found
