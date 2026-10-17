# SPDX-License-Identifier: GPL-2.0
"""VK Видео: search, channels and their videos through the VK API with the
anonymous token vkvideo.ru itself gets (no account), or with a person's
own after a sign-in confirmed on a phone (a link or QR code, as a TV
does); playback through mpv's yt-dlp hook. A channel is a VK community or
a person; its id is the owner id as text ("-55155418" for a community)."""
import os
import re
import threading
import time

from . import CACHE_DIR, CONFIG_DIR, load_json, net, save_json

API = "https://api.vk.com/method/%s"
API_VERSION = "5.274"
# The web client of vkvideo.ru: these are public, in its own scripts.
CLIENT_ID = "52461373"
CLIENT_SECRET = "o557NLIkAErNhakXrQ7A"
TOKEN_URL = "https://login.vk.com/?act=get_anonym_token"
SITE = "https://vkvideo.ru"
HEADERS = {"Origin": SITE, "Referer": SITE + "/"}
ANON_TOKEN = os.path.join(CACHE_DIR, "vk-anonym.json")
ACCOUNT = os.path.join(CONFIG_DIR, "video-vk.json")
CHANNEL_COUNT = 30
DESCRIPTION_MAX = 300
GROUP_FIELDS = "members_count,description,photo_200,screen_name"
USER_FIELDS = "followers_count,about,photo_200,screen_name"
LINK = re.compile(r"(?:https?://)?(?:[\w-]+\.)?(?:vkvideo\.ru|vk\.com|vk\.ru)/(.*)")
# auth.checkAuthCode: 0 waiting, 1 seen on the phone, 2 confirmed; others
# (expired, declined) end the sign-in.
WAITING = (0, 1)


class ApiError(Exception):
    def __init__(self, code, text):
        super().__init__("VK %s: %s" % (code, text))
        self.code = code


def _post(url, data):
    return net.get_json(url, data, HEADERS)


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


def _videos_in_order(resp, ids):
    """The videos of a catalog answer, in the order of ids, each once."""
    names = _owner_names(resp)
    items = {}
    for entry in (resp.get("catalog_videos") or []) + (resp.get("videos") or []):
        item = entry.get("video", entry)
        items["%s_%s" % (item.get("owner_id"), item.get("id"))] = item
    out, seen = [], set()
    for vid in ids:
        video = vid not in seen and vid in items and _video(items[vid], names)
        if video:
            seen.add(vid)
            out.append(video)
    return out


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
    login = "code"

    def __init__(self):
        self.lock = threading.Lock()
        # The anonymous token outlives a run of the program: kept on disk,
        # it spares a round trip at every start.
        anon = load_json(ANON_TOKEN, {}) or {}
        self.token = anon.get("token")
        self.token_until = anon.get("until", 0)
        self.user = load_json(ACCOUNT, None)
        self.pending = None  # the sign-in waiting for the phone

    # --- tokens -----------------------------------------------------------
    def _anon_token(self, fresh=False):
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
                try:
                    save_json(ANON_TOKEN, {"token": self.token, "until": self.token_until},
                              private=True)
                except OSError:
                    pass
            return self.token

    def _api(self, method, anonymous=False, **params):
        params.update(v=API_VERSION, lang="ru")
        given = params.pop("access_token", None)
        user = None if anonymous else self.user
        for fresh in (False, True):
            params["access_token"] = given or (user["token"] if user else self._anon_token(fresh))
            resp = _post(API % method + "?client_id=" + CLIENT_ID, params)
            err = resp.get("error")
            if not err:
                return resp["response"]
            if err.get("error_code") != 5 or fresh:  # 5: the token went stale
                raise ApiError(err.get("error_code"), err.get("error_msg"))
            if user:
                # The person's token was revoked: carry on as a guest.
                self.logout()
                user = None
        return None

    # --- account ----------------------------------------------------------
    def account(self):
        return {"name": self.user["name"]} if self.user else None

    def login_start(self):
        resp = self._api("auth.getAuthCode", anonymous=True, device_name="b8k-video",
                         client_id=CLIENT_ID, force_regenerate=1)
        self.pending = resp
        return {"url": resp["auth_url"], "code": resp["auth_code"]}

    def login_poll(self):
        if not self.pending:
            raise ApiError("login", "no sign-in started")
        resp = self._api("auth.checkAuthCode", anonymous=True,
                         auth_hash=self.pending["auth_hash"], client_id=CLIENT_ID)
        status = resp.get("status")
        token = resp.get("access_token")
        if not token and resp.get("super_app_token"):
            token = self._api("auth.exchangeSilentAuthToken", anonymous=True,
                              token=resp["super_app_token"],
                              uuid=self.pending["auth_id"])["access_token"]
        if not token:
            if status in WAITING:
                return None
            self.pending = None
            raise ApiError("login", "the code expired or was declined")
        self.pending = None
        me = self._api("users.get", anonymous=True, access_token=token)
        name = ("%s %s" % (me[0].get("first_name", ""), me[0].get("last_name", ""))).strip() \
            if me else ""
        self.user = {"token": token, "id": resp.get("user_id") or (me and me[0]["id"]),
                     "name": name or "VK"}
        save_json(ACCOUNT, self.user, private=True)
        return self.account()

    def logout(self):
        self.user = None
        self.pending = None
        try:
            os.remove(ACCOUNT)
        except OSError:
            pass

    def subscriptions(self):
        """The communities the person follows that have videos."""
        resp = self._api("groups.get", extended=1, count=1000, fields=GROUP_FIELDS)
        groups = resp.get("items") or []
        ids = [g["id"] for g in groups]
        with_videos = set()
        for i in range(0, len(ids), 400):
            got = self._api("groups.getById", group_ids=",".join(map(str, ids[i:i + 400])),
                            fields="counters")
            for g in got.get("groups") or []:
                if (g.get("counters") or {}).get("videos"):
                    with_videos.add(g["id"])
        return [_group(g) for g in groups if g["id"] in with_videos]

    # --- videos -----------------------------------------------------------
    def owns(self, text):
        return LINK.match(text.strip()) is not None

    def search(self, query):
        resp = self._api("catalog.getVideoSearchWeb2", q=query)
        # The catalog's blocks tell the order; clips (vertical, short) are
        # in blocks of their own and left out.
        ids = []
        for section in (resp.get("catalog") or {}).get("sections") or []:
            for block in section.get("blocks") or []:
                if block.get("data_type") == "catalog_videos":
                    ids.extend(block.get("videos_ids") or [])
        return _videos_in_order(resp, ids)

    def home(self):
        cat = self._api("catalog.getVideo")
        sections = (cat.get("catalog") or {}).get("sections") or []
        if not sections:
            return None
        resp = self._api("catalog.getSection", section_id=sections[0]["id"])
        ids = []
        for block in (resp.get("section") or {}).get("blocks") or []:
            if block.get("data_type") == "videos":
                ids.extend(block.get("videos_ids") or [])
        videos = _videos_in_order(resp, ids)
        return {"title": "Популярное в VK Видео", "videos": videos} if videos else None

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
