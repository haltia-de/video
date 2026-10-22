# SPDX-License-Identifier: GPL-2.0
"""The places videos come from. Every module of this package that defines
SOURCE is one; Видео lists them all and needs nothing else to know
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
import json
import os
import pkgutil
import sys



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
CONFIG_DIR = os.path.join(xdg("XDG_CONFIG_HOME", "~/.config"), "haltia-video")
DATA_DIR = os.path.join(xdg("XDG_DATA_HOME", "~/.local/share"), "haltia-video")
CACHE_DIR = os.path.join(xdg("XDG_CACHE_HOME", "~/.cache"), "haltia-video")


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


def load():
    """{id: SOURCE} of every source that imports; a broken one is skipped."""
    found = {}
    for info in pkgutil.iter_modules(__path__):
        try:
            mod = importlib.import_module("%s.%s" % (__name__, info.name))
        except Exception as exc:  # a source must never take the program down
            sys.stderr.write("haltia-video: source %s: %s\n" % (info.name, exc))
            continue
        src = getattr(mod, "SOURCE", None)
        if src is not None:
            found[src.id] = src
    return found
