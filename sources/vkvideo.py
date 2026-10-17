# SPDX-License-Identifier: GPL-2.0
"""VK Видео: search, channels and their videos through the VK API with the
anonymous token vkvideo.ru itself gets (no account), playback through
mpv's yt-dlp hook. A channel is a VK community or a person; its id is the
owner id as text ("-55155418" for a community)."""
import json
import re
import threading
import time
import urllib.parse
import urllib.request

API = "https://api.vk.com/method/%s"
API_VERSION = "5.274"
# The web client of vkvideo.ru: these are public, in its own scripts.
CLIENT_ID = "52461373"
CLIENT_SECRET = "o557NLIkAErNhakXrQ7A"
TOKEN_URL = "https://login.vk.com/?act=get_anonym_token"
SITE = "https://vkvideo.ru"
UA = "Mozilla/5.0 (X11; Linux armv7l; rv:151.0) Gecko/20100101 Firefox/151.0"
CHANNEL_COUNT = 30
DESCRIPTION_MAX = 300
GROUP_FIELDS = "members_count,description,photo_200,screen_name"
USER_FIELDS = "followers_count,about,photo_200,screen_name"
LINK = re.compile(r"(?:https?://)?(?:[\w-]+\.)?(?:vkvideo\.ru|vk\.com|vk\.ru)/(.*)")


class ApiError(Exception):
    def __init__(self, code, text):
        super().__init__("VK %s: %s" % (code, text))
        self.code = code


def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={
        "User-Agent": UA, "Origin": SITE, "Referer": SITE + "/"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read(8 * 1024 * 1024))


def _thumb(images):
    # The smallest 16:9 picture a card can take; the others are padded.
    best = None
    for img in images or []:
        if img.get("with_padding") or img.get("width", 0) < 320:
            continue
        if best is None or img["width"] < best["width"]:
            best = img
    if best is None and images:
        best = images[-1]
    return best["url"] if best else ""


def _short(text):
    text = " ".join((text or "").split())
    if len(text) > DESCRIPTION_MAX:
        text = text[:DESCRIPTION_MAX].rsplit(" ", 1)[0] + "…"
    return text


def _owner_names(resp):
    names = {}
    for g in resp.get("groups") or []:
        names[-g["id"]] = g.get("name") or ""
    for p in resp.get("profiles") or []:
        names[p["id"]] = ("%s %s" % (p.get("first_name", ""), p.get("last_name", ""))).strip()
    return names


def _video(item, names):
    owner = item.get("owner_id")
    if owner is None or not item.get("id"):
        return None
    return {
        "source": "vk",
        "id": "%d_%d" % (owner, item["id"]),
        "title": item.get("title") or "",
        "channel": names.get(owner, ""),
        "channel_id": str(owner),
        "duration": item.get("duration") or None,
        "thumb": _thumb(item.get("image")),
        "published": item.get("date"),
    }


def _group(g):
    return {
        "source": "vk",
        "id": str(-g["id"]),
        "title": g.get("name") or str(g["id"]),
        "handle": "@" + g["screen_name"] if g.get("screen_name") else "",
        "thumb": g.get("photo_200") or "",
        "subscribers": g.get("members_count"),
        "description": _short(g.get("description")),
    }


def _user(u):
    return {
        "source": "vk",
        "id": str(u["id"]),
        "title": ("%s %s" % (u.get("first_name", ""), u.get("last_name", ""))).strip()
                 or str(u["id"]),
        "handle": "@" + u["screen_name"] if u.get("screen_name") else "",
        "thumb": u.get("photo_200") or "",
        "subscribers": u.get("followers_count") or None,
        "description": _short(u.get("about")),
    }


class VKVideo:
    id = "vk"
    label = "VK Видео"

    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.token_until = 0

    def _token(self, fresh=False):
        with self.lock:
            if fresh or not self.token or time.time() > self.token_until - 600:
                resp = _post(TOKEN_URL, {
                    "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                    "scopes": "video_anonymous,photos_anonymous,profile_anonymous",
                    "isApiOauthAnonymEnabled": "false", "version": "1", "app_id": "6287487"})
                data = resp.get("data") or {}
                if not data.get("access_token"):
                    raise ApiError("token", resp)
                self.token = data["access_token"]
                self.token_until = data.get("expired_at") or time.time() + 3600
            return self.token

    def _api(self, method, **params):
        params.update(v=API_VERSION, lang="ru")
        for fresh in (False, True):
            params["access_token"] = self._token(fresh)
            resp = _post(API % method + "?client_id=" + CLIENT_ID, params)
            err = resp.get("error")
            if not err:
                return resp["response"]
            if err.get("error_code") != 5 or fresh:  # 5: the token went stale
                raise ApiError(err.get("error_code"), err.get("error_msg"))
        return None

    def owns(self, text):
        return LINK.match(text.strip()) is not None

    def search(self, query):
        resp = self._api("catalog.getVideoSearchWeb2", q=query)
        names = _owner_names(resp)
        items = {}
        for entry in resp.get("catalog_videos") or []:
            item = entry.get("video") or {}
            items["%s_%s" % (item.get("owner_id"), item.get("id"))] = item
        # The catalog's blocks tell the order; clips (vertical, short) are
        # in blocks of their own and left out.
        out, seen = [], set()
        for section in (resp.get("catalog") or {}).get("sections") or []:
            for block in section.get("blocks") or []:
                if block.get("data_type") != "catalog_videos":
                    continue
                for vid in block.get("videos_ids") or []:
                    video = vid not in seen and items.get(vid) and _video(items[vid], names)
                    if video:
                        seen.add(vid)
                        out.append(video)
        return out

    def channel_videos(self, channel):
        resp = self._api("video.get", owner_id=channel["id"], count=CHANNEL_COUNT, extended=1)
        names = _owner_names(resp)
        names.setdefault(int(channel["id"]), channel["title"])
        out = []
        for item in resp.get("items") or []:
            video = _video(item, names)
            if video:
                video["channel"] = channel["title"]
                video["channel_id"] = channel["id"]
                out.append(video)
        out.sort(key=lambda v: v["published"] or 0, reverse=True)
        return out

    def _owners(self, ids):
        """Channels for owner ids (negative for communities), in order."""
        groups = [str(-i) for i in ids if i < 0]
        users = [str(i) for i in ids if i > 0]
        found = {}
        if groups:
            resp = self._api("groups.getById", group_ids=",".join(groups), fields=GROUP_FIELDS)
            for g in resp.get("groups") or []:
                found[-g["id"]] = _group(g)
        if users:
            for u in self._api("users.get", user_ids=",".join(users), fields=USER_FIELDS):
                found[u["id"]] = _user(u)
        return [found[i] for i in ids if i in found]

    def search_channels(self, query):
        query = query.strip()
        if self.owns(query) or query.startswith("@") or re.fullmatch(r"-?\d+", query):
            chan = self.find_channel(query)
            return [chan] if chan else []
        resp = self._api("catalog.getVideoSearchWeb2", q=query, content_type="author")
        ids = []
        for section in (resp.get("catalog") or {}).get("sections") or []:
            for block in section.get("blocks") or []:
                for item in block.get("search_author_items") or []:
                    if item.get("id") and item["id"] not in ids:
                        ids.append(item["id"])
        return self._owners(ids) if ids else []

    def find_channel(self, text):
        text = text.strip()
        if re.fullmatch(r"-?\d+", text):
            owners = self._owners([int(text)])
            return owners[0] if owners else None
        m = LINK.match(text)
        name = m.group(1) if m else text
        name = name.split("?")[0].split("#")[0].strip("/")
        if name.startswith("video/"):
            name = name[len("video/"):]
        name = name.split("/")[0].lstrip("@")
        if not name:
            return None
        m = re.fullmatch(r"(?:club|public|event)(\d+)", name) or re.fullmatch(r"id(\d+)", name)
        if m:
            owner = int(m.group(1)) * (1 if name.startswith("id") else -1)
        else:
            res = self._api("utils.resolveScreenName", screen_name=name)
            if not res or res.get("type") not in ("group", "page", "event", "user"):
                return None
            owner = res["object_id"] * (1 if res["type"] == "user" else -1)
        owners = self._owners([owner])
        return owners[0] if owners else None

    def watch_url(self, video):
        return "%s/video%s" % (SITE, video["id"])


SOURCE = VKVideo()
