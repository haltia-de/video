# SPDX-License-Identifier: GPL-2.0
"""YouTube: search through yt-dlp (no account, no API key), a channel's
newest videos from its public Atom feed (one small request instead of a
page of JavaScript), playback through mpv's yt-dlp hook."""
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=%s"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
UA = "Mozilla/5.0 (X11; Linux armv7l; rv:151.0) Gecko/20100101 Firefox/151.0"
SEARCH_COUNT = 24


def _thumb(video_id):
    # 320x180: enough for a card, a fraction of the larger ones.
    return "https://i.ytimg.com/vi/%s/mqdefault.jpg" % video_id


def _ydl(extra=None):
    import yt_dlp  # slow to import: only when first needed

    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "socket_timeout": 20}
    opts.update(extra or {})
    return yt_dlp.YoutubeDL(opts)


def _video(entry):
    vid = entry.get("id")
    if not vid or entry.get("ie_key") not in (None, "Youtube"):
        return None
    return {
        "source": "youtube",
        "id": vid,
        "title": entry.get("title") or vid,
        "channel": entry.get("channel") or entry.get("uploader") or "",
        "channel_id": entry.get("channel_id") or "",
        "duration": entry.get("duration"),
        "thumb": _thumb(vid),
        "published": entry.get("timestamp"),
    }


class YouTube:
    id = "youtube"
    label = "YouTube"

    def warm_up(self):
        import yt_dlp  # noqa: F401  (the import is the slow part)

    def search(self, query):
        with _ydl() as ydl:
            info = ydl.extract_info("ytsearch%d:%s" % (SEARCH_COUNT, query), download=False)
        out = []
        for entry in info.get("entries") or []:
            video = _video(entry or {})
            # Live streams have no duration; they play, but slowly start.
            if video:
                out.append(video)
        return out

    def channel_videos(self, channel):
        req = urllib.request.Request(FEED % channel["id"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            root = ET.fromstring(resp.read(2 * 1024 * 1024))
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
            out.append({
                "source": "youtube",
                "id": vid,
                "title": entry.findtext("atom:title", vid, NS),
                "channel": entry.findtext("atom:author/atom:name", channel["title"], NS),
                "channel_id": channel["id"],
                "duration": None,
                "thumb": _thumb(vid),
                "published": when,
            })
        return out

    def find_channel(self, text):
        text = text.strip()
        if re.fullmatch(r"UC[\w-]{22}", text):
            url = "https://www.youtube.com/channel/" + text
        elif text.startswith("@"):
            url = "https://www.youtube.com/" + text
        elif text.startswith("http"):
            url = text
        else:
            url = "https://www.youtube.com/@" + text.replace(" ", "")
        with _ydl({"playlistend": 1}) as ydl:
            info = ydl.extract_info(url.rstrip("/") + "/videos", download=False)
        cid = info.get("channel_id") or info.get("id")
        if not cid or not cid.startswith("UC"):
            return None
        return {"source": "youtube", "id": cid,
                "title": info.get("channel") or info.get("uploader") or info.get("title") or cid}

    def watch_url(self, video):
        return "https://www.youtube.com/watch?v=" + video["id"]


SOURCE = YouTube()
