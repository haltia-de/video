# SPDX-License-Identifier: GPL-2.0
"""YouTube: search through yt-dlp (no account, no API key), a channel's
newest videos from its public Atom feed (one small request instead of a
page of JavaScript), playback through mpv's yt-dlp hook or, for
haltia-vplay on the tablet, through stream(): this process's yt-dlp is
already started and warm, a new one for every video took 6 s more there.

YouTube lets no program sign in on its own any more, and it shows no
trends to a guest: signing in is the cookies of a browser where one is
signed in. yt-dlp reads them from Firefox's or Chromium's profile itself
where it can see one (not from Flatpak's sandbox); everywhere, the tablet
too, a cookies.txt file put in place works. That file then goes into the
vault (the keyring, where one answers). With it, Главная has the account's recommendations,
the account's channels can be followed here, and searches and playback go
as that account."""
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

from . import CONFIG_DIR, load_json, net, save_json, stream_of
from .vault import Vault

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=%s"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
SEARCH_COUNT = 24
HOME_COUNT = 24
CHANNEL_COUNT = 12
AVATAR_SIZE = 176
# The "channels only" filter of YouTube's search page.
CHANNELS_ONLY = "EgIQAg=="
DESCRIPTION_MAX = 300


def _thumb(video_id):
    # 320x180: enough for a card, a fraction of the larger ones.
    return "https://i.ytimg.com/vi/%s/mqdefault.jpg" % video_id


# Видео shows no subtitles. yt-dlp makes every automatic caption into ~157
# translations, a URL for each of them in each format: seconds of the
# tablet's CPU for every video. False drops the captions YouTube sends
# before yt-dlp looks at them.
SUBTITLES = False

COOKIES = "youtube-cookies.txt"
# yt-dlp's names, in the order offered; where each keeps its profile.
BROWSERS = (
    ("firefox", "Firefox", ("~/.mozilla/firefox", "~/.config/mozilla/firefox",
                            "~/snap/firefox/common/.mozilla/firefox",
                            "~/.var/app/org.mozilla.firefox/.mozilla/firefox",
                            "~/.var/app/org.mozilla.firefox/config/mozilla/firefox")),
    ("chromium", "Chromium", ("~/.config/chromium", "~/snap/chromium/common/chromium",
                              "~/.var/app/org.chromium.Chromium/config/chromium")),
)


def browsers(exists=os.path.exists, sandboxed=None):
    """[(id, label)] of the browsers yt-dlp can take the cookies from here:
    none in Flatpak (its sandbox hides the profiles)."""
    if sandboxed if sandboxed is not None else exists("/.flatpak-info"):
        return []
    return [(bid, name) for bid, name, dirs in BROWSERS
            if any(exists(os.path.expanduser(d)) for d in dirs)]


def _skip_captions():
    """yt-dlp's YouTube extractor, taught (once) to drop the captions of
    the player answers; nothing when its insides have changed."""
    from yt_dlp.extractor.youtube import YoutubeIE

    answers = getattr(YoutubeIE, "_extract_player_responses", None)
    if answers is None or getattr(answers, "without_captions", False):
        return

    def without_captions(self, *args, **kwargs):
        prs, player_url = answers(self, *args, **kwargs)
        for pr in prs:
            if isinstance(pr, dict):
                pr.pop("captions", None)
        return prs, player_url

    without_captions.without_captions = True
    YoutubeIE._extract_player_responses = without_captions


def _ydl(extra=None, cookies=None):
    import yt_dlp  # slow to import: only when first needed

    if not SUBTITLES:
        _skip_captions()
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "socket_timeout": 20,
            # A list of results carries no dates, only "3 days ago": this
            # turns that into a time (to the day) for the cards.
            "extractor_args": {"youtubetab": {"approximate_date": [""]}}}
    opts.update(cookies or {})  # cookiefile or cookiesfrombrowser
    opts.update(extra or {})
    return yt_dlp.YoutubeDL(opts)


def _video(entry):
    vid = entry.get("id")
    if not vid or entry.get("ie_key") not in (None, "Youtube"):
        return None
    video = {
        "source": "youtube",
        "id": vid,
        "title": entry.get("title") or vid,
        "channel": entry.get("channel") or entry.get("uploader") or "",
        "channel_id": entry.get("channel_id") or "",
        "duration": entry.get("duration"),
        "thumb": _thumb(vid),
        "published": entry.get("timestamp"),
    }
    if "/shorts/" in (entry.get("url") or ""):
        video["short"] = True
    return video


def _avatar(thumbs):
    url = ""
    for thumb in thumbs or []:
        if thumb.get("id") == "avatar_uncropped":
            url = thumb["url"]
            break
        if thumb.get("width") and thumb.get("width") == thumb.get("height"):
            url = thumb["url"]  # the last square one is the largest
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    # The size is in the URL's tail: ask for a small one, not 900x900.
    return re.sub(r"=[^=/]*$", "=s%d-c-k-c0x00ffffff-no-rj" % AVATAR_SIZE, url)


def _channel(info, cid):
    handle = info.get("uploader_id") or ""
    desc = " ".join((info.get("description") or "").split())
    if len(desc) > DESCRIPTION_MAX:
        desc = desc[:DESCRIPTION_MAX].rsplit(" ", 1)[0] + "…"
    return {
        "source": "youtube",
        "id": cid,
        "title": info.get("channel") or info.get("uploader") or info.get("title") or cid,
        "handle": handle if handle.startswith("@") else "",
        "thumb": _avatar(info.get("thumbnails")),
        "subscribers": info.get("channel_follower_count"),
        "description": desc,
    }


def _is_link(text):
    return (re.fullmatch(r"UC[\w-]{22}", text) is not None
            or text.startswith(("@", "http")) or "youtube.com/" in text)


class YouTube:
    id = "youtube"
    label = "YouTube"
    login = "cookies"

    def __init__(self, http=net, config_dir=CONFIG_DIR, vault=None, sandboxed=None):
        """http: what fetches the channel feeds (request(url, limit=...));
        the sources package's net, or recorded answers in the tests (with
        a vault of files only)."""
        self.http = http
        self.vault = vault or Vault(config_dir)
        self.sandboxed = sandboxed
        self.cookies = os.path.join(config_dir, COOKIES)  # where the person puts the file
        # Not secret (the cookies are): signed in, and how.
        self.account_path = os.path.join(config_dir, "video-youtube.json")
        put = self.cookies.replace(os.path.expanduser("~"), "~")
        self.login_hint = ("Выгрузите cookies сайта youtube.com из браузера, где вы "
                           "вошли в аккаунт (расширение «Get cookies.txt LOCALLY»), и "
                           "положите файл сюда: " + put)
        if self.browsers():
            self.login_hint = ("Войдите в YouTube в браузере и нажмите кнопку с его "
                               "названием. Или положите файл cookies сюда: " + put)

    def browsers(self):
        return browsers(sandboxed=self.sandboxed)

    def _cookies(self):
        """The yt-dlp options that sign in, or None."""
        account = load_json(self.account_path)
        if not account:
            return None
        if account.get("browser"):
            return {"cookiesfrombrowser": (account["browser"],)}
        path = self.vault.as_file(COOKIES)
        return {"cookiefile": path} if path else None

    def _signed_in(self):
        return self._cookies() is not None

    def _ydl(self, extra=None):
        return _ydl(extra, self._cookies())

    def account(self):
        account = load_json(self.account_path)
        if account and (account.get("browser") or self.vault.get(COOKIES) is not None):
            return account
        return None

    def login_check(self, browser=None):
        """Signs in with the browser's cookies (an id of browsers()), else
        with the file put in place (or the one kept before)."""
        if browser:
            cookies = {"cookiesfrombrowser": (browser,)}
        elif os.path.exists(self.cookies):
            cookies = {"cookiefile": self.cookies}
        else:
            path = self.vault.as_file(COOKIES)
            if not path:
                return None
            cookies = {"cookiefile": path}
        # The account's own list of channels opens only to the account.
        with _ydl({"playlistend": 1}, cookies) as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/channels", download=False)
        if not info or info.get("entries") is None:
            return None
        account = {"name": "аккаунт Google"}
        if browser:
            account["browser"] = browser
            account["name"] = "аккаунт Google (%s)" % dict(
                (b, n) for b, n, _d in BROWSERS).get(browser, browser)
        else:
            self.vault.take(COOKIES)
        save_json(self.account_path, account)
        return account

    def logout(self):
        try:
            os.remove(self.account_path)
        except OSError:
            pass
        self.vault.drop(COOKIES)

    def subscriptions(self):
        with self._ydl() as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/channels", download=False)
        out = []
        for entry in info.get("entries") or []:
            cid = (entry or {}).get("channel_id") or (entry or {}).get("id") or ""
            if cid.startswith("UC"):
                out.append(_channel(entry, cid))
        return out

    def home(self):
        if not self._signed_in():
            return None
        with self._ydl({"playlistend": HOME_COUNT}) as ydl:
            info = ydl.extract_info(":ytrec", download=False)
        videos = [v for v in (_video(e or {}) for e in info.get("entries") or []) if v]
        return {"title": "Рекомендации YouTube", "videos": videos} if videos else None

    def ytdl_options(self):
        """For mpv's ytdl-raw-options (yt-dlp's command-line names)."""
        cookies = self._cookies() or {}
        if "cookiesfrombrowser" in cookies:
            return {"cookies-from-browser": cookies["cookiesfrombrowser"][0]}
        return {"cookies": cookies["cookiefile"]} if cookies else {}

    def warm_up(self):
        # The imports are the slow part: yt-dlp, and its YouTube extractor,
        # which the first stream() would load.
        _ydl().close()

    def search(self, query):
        with self._ydl() as ydl:
            info = ydl.extract_info("ytsearch%d:%s" % (SEARCH_COUNT, query), download=False)
        out = []
        for entry in info.get("entries") or []:
            video = _video(entry or {})
            # Live streams have no duration; they play, but slowly start.
            if video:
                out.append(video)
        return out

    def channel_videos(self, channel):
        root = ET.fromstring(self.http.request(FEED % channel["id"], limit=2 * 1024 * 1024))
        out = []
        for entry in root.findall("atom:entry", NS):
            vid = entry.findtext("yt:videoId", "", NS)
            if not vid:
                continue
            published = entry.findtext("atom:published", "", NS)
            try:
                when = datetime.fromisoformat(published).timestamp()
            except ValueError:
                when = None
            video = {
                "source": "youtube",
                "id": vid,
                "title": entry.findtext("atom:title", vid, NS),
                "channel": entry.findtext("atom:author/atom:name", channel["title"], NS),
                "channel_id": channel["id"],
                "duration": None,
                "thumb": _thumb(vid),
                "published": when,
            }
            link = entry.find("atom:link[@rel='alternate']", NS)
            if link is not None and "/shorts/" in link.get("href", ""):
                video["short"] = True
            out.append(video)
        return out

    def owns(self, text):
        text = text.strip()
        return (re.fullmatch(r"UC[\w-]{22}", text) is not None
                or "youtube.com/" in text or "youtu.be/" in text)

    def search_channels(self, query):
        query = query.strip()
        if _is_link(query):
            chan = self.find_channel(query)
            return [chan] if chan else []
        url = "https://www.youtube.com/results?" + urllib.parse.urlencode(
            {"search_query": query, "sp": CHANNELS_ONLY})
        with self._ydl({"playlistend": CHANNEL_COUNT}) as ydl:
            info = ydl.extract_info(url, download=False)
        out = []
        for entry in info.get("entries") or []:
            cid = (entry or {}).get("channel_id") or ""
            if cid.startswith("UC"):
                out.append(_channel(entry, cid))
        return out

    def find_channel(self, text):
        text = text.strip()
        if text.startswith("youtube.com/") or text.startswith("www.youtube.com/"):
            text = "https://" + text
        if re.fullmatch(r"UC[\w-]{22}", text):
            url = "https://www.youtube.com/channel/" + text
        elif text.startswith("@"):
            url = "https://www.youtube.com/" + text
        elif text.startswith("http"):
            url = text
        else:
            url = "https://www.youtube.com/@" + text.replace(" ", "")
        with self._ydl({"playlistend": 1}) as ydl:
            info = ydl.extract_info(url.rstrip("/") + "/videos", download=False)
        cid = info.get("channel_id") or info.get("id")
        if not cid or not cid.startswith("UC"):
            return None
        return _channel(info, cid)

    def watch_url(self, video):
        return "https://www.youtube.com/watch?v=" + video["id"]

    def stream(self, video, fmt):
        """What to play of video in format fmt (yt-dlp's format words), for
        a player that asks no yt-dlp of its own: {"urls": [video, audio] or
        [both], "user_agent"}. The URLs live some hours."""
        # The web client's formats are all SABR now, left out, but yt-dlp
        # takes their URLs apart first: seconds of the tablet's CPU.
        with self._ydl({"format": fmt, "extract_flat": False,
                        "js_runtimes": {"quickjs": {}},
                        "extractor_args": {"youtube": {"player_client": ["default", "-web"]}}}) as ydl:
            return stream_of(ydl.extract_info(self.watch_url(video), download=False))


SOURCE = YouTube()
