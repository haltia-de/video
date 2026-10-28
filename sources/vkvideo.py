# SPDX-License-Identifier: GPL-2.0
"""VK Видео: search, channels and their videos through the VK API with the
anonymous token vkvideo.ru itself gets (no account), or with a person's
own after a sign-in confirmed on a phone (a link or QR code, as a TV
does); playback through mpv's yt-dlp hook. A channel is a VK community or
a person; its id is the owner id as text ("-55155418" for a community)."""
import json
import os
import re
import threading
import time

from haltia_kit.vault import Locked

from . import Denied as SourceDenied
from . import (CACHE_DIR, CONFIG_DIR, LARGE_PICTURES, browser_stream_of, load_json, net, save_json,
               stream_of, vault_in)

API = "https://api.vk.com/method/%s"
API_VERSION = "5.274"
# The web client of vkvideo.ru: these are public, in its own scripts.
CLIENT_ID = "52461373"
CLIENT_SECRET = "o557NLIkAErNhakXrQ7A"
TOKEN_URL = "https://login.vk.com/?act=get_anonym_token"
SITE = "https://vkvideo.ru"
ACCOUNT = "video-vk.json"  # in the vault: {"token", "id", "name"}
HEADERS = {"Origin": SITE, "Referer": SITE + "/"}
CHANNEL_COUNT = 30
DESCRIPTION_MAX = 300
GROUP_FIELDS = "members_count,description,photo_200,screen_name"
USER_FIELDS = "followers_count,about,photo_200,screen_name"
LINK = re.compile(r"(?:https?://)?(?:[\w-]+\.)?(?:vkvideo\.ru|vk\.com|vk\.ru)/(.*)")
# auth.checkAuthCode: 0 waiting, 1 seen on the phone, 2 confirmed,
# 3 declined, 4 expired, 5 asks for the one-time code on the phone.
# NEED_OTP must keep this QR's auth_hash.
WAITING = (0, 1)


class ApiError(Exception):
    def __init__(self, code, text):
        super().__init__("VK %s: %s" % (code, text))
        self.code = code


# VK's answers that refuse what was asked rather than fail: access denied
# (15), to a private profile (30), an album (200, 204), a group's videos
# (203), videos at all (201).
DENIED = (15, 30, 200, 201, 203, 204)


class Denied(ApiError, SourceDenied):
    pass


def _skip_subtitles():
    """yt-dlp's VK extractor, taught (once) to drop the subtitles of the
    player's answer: Видео shows none, and yt-dlp 2026.08.19 fails on one
    without a title (yt-dlp pull request 16761), so the video did not play
    at all. Nothing when its insides have changed."""
    from yt_dlp.extractor.vk import VKIE

    payload = getattr(VKIE, "_download_payload", None)
    if payload is None or getattr(payload, "without_subtitles", False):
        return

    def without_subtitles(self, *args, **kwargs):
        answer = payload(self, *args, **kwargs)
        try:
            for params in answer[-1]["player"]["params"]:
                params.pop("subs", None)
        except (LookupError, TypeError, AttributeError):
            pass
        return answer

    without_subtitles.without_subtitles = True
    VKIE._download_payload = without_subtitles


# The smallest picture a card can take (see LARGE_PICTURES).
THUMB_MIN = 640 if LARGE_PICTURES else 320


def _thumb(images, padded=False):
    # The smallest 16:9 picture a card can take; the others are padded
    # (an album's cover is padded in every size: padded=True takes them).
    best = None
    for img in images or []:
        if (img.get("with_padding") and not padded) or img.get("width", 0) < THUMB_MIN:
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
    # Restricted (to an account, by age): it would not play, and VK sends
    # no picture for it either.
    if (item.get("restriction") or {}).get("can_play") == 0:
        return None
    video = {
        "source": "vk",
        "id": "%d_%d" % (owner, item["id"]),
        "title": item.get("title") or "",
        "channel": names.get(owner, ""),
        "channel_id": str(owner),
        "duration": item.get("duration") or None,
        "thumb": _thumb(item.get("image")),
        "published": item.get("date"),
    }
    if item.get("type") == "short_video":
        video["short"] = True
    return video


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

    def __init__(self, http=net, config_dir=CONFIG_DIR, cache_dir=CACHE_DIR, clock=time.time,
                 vault=None):
        """http: what fetches (get_json(url, data, headers)); the sources
        package's net, or recorded answers in the tests, which also give a
        clock (a recorded token's expiry is a date that passes) and a vault
        of files only."""
        self.http = http
        self.clock = clock
        self.vault = vault or vault_in(config_dir)
        self.anon_path = os.path.join(cache_dir, "vk-anonym.json")
        self.lock = threading.Lock()
        # The anonymous token outlives a run of the program: kept on disk,
        # it spares a round trip at every start.
        anon = load_json(self.anon_path, {}) or {}
        self.token = anon.get("token")
        self.token_until = anon.get("until", 0)
        self.locked = False
        self.user = self._kept_user()
        self.pending = None  # the sign-in waiting for the phone

    def _kept_user(self):
        try:
            return json.loads(self.vault.get(ACCOUNT) or "null")
        except ValueError:
            return None
        except Locked:  # a guest until unlock(); the token stays kept
            self.locked = True
            return None

    def _post(self, url, data):
        return self.http.get_json(url, data, HEADERS)

    # --- tokens -----------------------------------------------------------
    def _anon_token(self, fresh=False):
        with self.lock:
            if fresh or not self.token or self.clock() > self.token_until - 600:
                resp = self._post(TOKEN_URL, {
                    "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                    "scopes": "video_anonymous,photos_anonymous,profile_anonymous",
                    "isApiOauthAnonymEnabled": "false", "version": "1", "app_id": "6287487"})
                data = resp.get("data") or {}
                if not data.get("access_token"):
                    raise ApiError("token", resp)
                self.token = data["access_token"]
                self.token_until = data.get("expired_at") or self.clock() + 3600
                try:
                    save_json(self.anon_path, {"token": self.token, "until": self.token_until},
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
            resp = self._post(API % method + "?client_id=" + CLIENT_ID, params)
            err = resp.get("error")
            if not err:
                return resp["response"]
            if err.get("error_code") != 5 or fresh:  # 5: the token went stale
                raise (Denied if err.get("error_code") in DENIED else ApiError)(
                    err.get("error_code"), err.get("error_msg"))
            if user:
                # The person's token was revoked: carry on as a guest.
                if self.user is not user:
                    raise ApiError("account", "Аккаунт изменился.")
                self.logout()
                user = None
        return None

    # --- account ----------------------------------------------------------
    def account(self):
        return {"name": self.user["name"]} if self.user else None

    def bookmark_client(self):
        return VKBookmarks(self, dict(self.user)) if self.user else None

    def unlock(self):
        self.locked = False
        self.user = self._kept_user()

    def login_start(self):
        resp = self._api("auth.getAuthCode", anonymous=True, device_name="haltia-video",
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
            if status == 5:
                return {"need_code": True}
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
        try:
            self.vault.put(ACCOUNT, json.dumps(self.user, ensure_ascii=False))
        except Locked:
            self.user, self.locked = None, True
            raise
        return self.account()

    def login_submit(self, code):
        pending = self.pending
        if not pending:
            return "expired"
        # VK ID's QR flow uses validateAuthCode, not the auth_code in the
        # QR URL. A wrong code can be retried with the same auth_hash.
        resp = self._api("auth.validateAuthCode", anonymous=True,
                         auth_hash=pending["auth_hash"], validation_code=code,
                         client_id=CLIENT_ID)
        status = resp.get("status")
        if status == 0:
            return "accepted"  # checkAuthCode still supplies the token
        if status == 1:
            return "incorrect"
        if status == 2:
            if self.pending is pending:
                self.pending = None
            return "expired"
        raise ApiError("login", "unexpected code validation status")

    def logout(self):
        self.user = None
        self.pending = None
        self.vault.drop(ACCOUNT)

    def subscriptions(self):
        """The communities the person follows that have videos."""
        resp = self._api("groups.get", extended=1, count=1000, fields=GROUP_FIELDS)
        groups = resp.get("items") or []
        ids = [g["id"] for g in groups]
        with_videos = set()
        # VK omits counters when getById names more than one community.
        # execute keeps these individual lookups in one round trip (25
        # calls at most), rather than hundreds of trips on the tablet.
        for i in range(0, len(ids), 25):
            calls = ['API.groups.getById({"group_ids":"%d","fields":"counters"})' % gid
                     for gid in ids[i:i + 25]]
            got = self._api("execute", code="return [%s];" % ",".join(calls))
            if not isinstance(got, list) or len(got) != len(calls):
                raise ApiError("subscriptions", "could not check the communities' videos")
            for answer in got:
                if not isinstance(answer, dict):  # execute uses false for a failed call
                    raise ApiError("subscriptions", "could not check a community's videos")
                for g in answer.get("groups") or []:
                    if (g.get("counters") or {}).get("videos"):
                        with_videos.add(g["id"])
        return [_group(g) for g in groups if g["id"] in with_videos]

    # --- videos -----------------------------------------------------------
    def owns(self, text):
        return LINK.match(text.strip()) is not None

    def search(self, query):
        resp = self._api("catalog.getVideoSearchWeb2", q=query)
        # The catalog's blocks tell the order; clips (vertical, short) are
        # in a block of their own ("videos"), after the videos found before it.
        ids = []
        for section in (resp.get("catalog") or {}).get("sections") or []:
            for block in section.get("blocks") or []:
                if block.get("data_type") in ("catalog_videos", "videos"):
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

    def playlists(self, channel):
        out, seen, offset = [], set(), 0
        while True:
            resp = self._api("video.getAlbums", owner_id=channel["id"],
                             count=100, offset=offset, extended=1)
            items = resp.get("items") or []
            for item in items:
                pid = item.get("id")
                if pid is None or pid <= 0 or pid in seen:
                    continue
                seen.add(pid)
                out.append({"source": self.id, "owner_id": channel["id"],
                            "id": str(pid), "title": item.get("title") or "Плейлист",
                            "count": item.get("count"),
                            "thumb": _thumb(item.get("image"), padded=True)})
            offset += len(items)
            if not items or offset >= resp.get("count", offset):
                return out

    def playlist_videos(self, playlist, offset=0):
        resp = self._api("video.get", owner_id=playlist["owner_id"],
                         album_id=playlist["id"], count=CHANNEL_COUNT, offset=offset,
                         extended=1)
        items = resp.get("items") or []
        names = _owner_names(resp)
        # Keep the playlist's order and each video's actual channel; a
        # playlist can contain another community's videos too.
        videos = []
        for item in items:
            video = _video(item, names)
            if video:
                videos.append(video)
        after = offset + len(items)
        return {"videos": videos,
                "next": after if items and after < resp.get("count", after) else None}

    def resolve_bookmark(self, url):
        match = LINK.fullmatch(url.strip())
        if not match:
            raise ValueError("Нужна ссылка VK Видео.")
        path = match.group(1).split("?")[0].strip("/")
        video = re.fullmatch(r"video(-?\d+_\d+)", path)
        album = re.fullmatch(r"(?:playlist|video/playlist)/?(-?\d+)_(\d+)", path)
        # VK also uses /video/playlist/<owner>_<album>.
        if video:
            resp = self._api("video.get", videos=video.group(1), extended=1)
            items = resp.get("items") or []
            obj = _video(items[0], _owner_names(resp)) if items else None
            if obj:
                return "video", obj
            raise ValueError("Видео недоступно этому аккаунту.")
        if album:
            chan = self.find_channel(album.group(1))
            obj = next((p for p in self.playlists(chan) if p["id"] == album.group(2)), None)
            if obj:
                obj["owner_title"] = chan["title"]
                return "playlist", obj
            raise ValueError("Плейлист не найден или недоступен.")
        chan = self.find_channel(url)
        if chan:
            return "channel", chan
        raise ValueError("Канал не найден или недоступен.")

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

    def warm_up(self):
        _skip_subtitles()  # imports yt-dlp and its VK extractor: the slow part

    @property
    def resolve_on_desktop(self):
        # The player's own yt-dlp has no VK API account. Resolve private
        # videos here even on a desktop, without handing it the token.
        return bool(self.user)

    def stream(self, video, fmt, _browser=False):
        """The stream through this process's yt-dlp (see sources): a new
        one for every video took 17 s on the tablet."""
        import yt_dlp  # slow to import: only when first needed

        _skip_subtitles()
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                               "socket_timeout": 20, "format": fmt}) as ydl:
            if self.user:
                resp = self._api("video.get", videos=video["id"], extended=0)
                items = resp.get("items") or []
                files = (items[0].get("files") or {}) if items else {}
                formats = []
                for key, url in files.items():
                    match = re.fullmatch(r"mp4_(\d+)", key)
                    if match and isinstance(url, str) and url.startswith(("https://", "http://")):
                        formats.append({"format_id": key, "url": url, "ext": "mp4",
                                        "height": int(match.group(1)), "vcodec": "avc1",
                                        "acodec": "mp4a", "http_headers": {"User-Agent": net.UA}})
                if formats:
                    # VK binds CDN links to the API request's User-Agent;
                    # mpv's default causes HTTP 400 even for a valid URL.
                    info = {"id": video["id"], "title": video["title"], "formats": formats,
                            "duration": video.get("duration"),
                            "extractor": "vk", "extractor_key": "VK"}
                    result = ydl.process_ie_result(info, download=False)
                    return browser_stream_of(result) if _browser else stream_of(result)
            result = ydl.extract_info(self.watch_url(video), download=False)
            return browser_stream_of(result) if _browser else stream_of(result)

    def browser_stream(self, video, fmt):
        return self.stream(video, fmt, _browser=True)


class VKBookmarks:
    """A captured account: never fall back to a guest or a replacement
    token during a bookmark write. fave is unavailable to some TV tokens.
    Capability probes are read-only, per sign-in, before offering sync.
    """
    def __init__(self, source, user):
        self.source = source
        self.user = user
        self.id = str(user["id"])
        self.name = user["name"]

    def api(self, method, **params):
        if not self.active():
            raise ApiError("account", "Аккаунт изменился; синхронизация остановлена.")
        params.update(v=API_VERSION, lang="ru", access_token=self.user["token"])
        resp = self.source._post(API % method + "?client_id=" + CLIENT_ID, params)
        if resp.get("error"):
            error = resp["error"]
            # Never expose request parameters (which contain the token).
            raise ApiError(error.get("error_code"), "Не удалось обновить закладки VK.")
        return resp["response"]

    def active(self):
        return self.source.user == self.user

    def capabilities(self):
        kinds = []
        for kind, calls in (("video", [("fave.get", {"item_type": "video"})]),
                            ("channel", [("fave.getPages", {"type": "groups"}),
                                         ("fave.getPages", {"type": "users"})])):
            try:
                for method, params in calls:
                    self.api(method, count=1, **params)
            except ApiError as exc:
                if exc.code not in (3, 7, 15, 27, 28):
                    raise
            else:
                kinds.append(kind)
        # No typed playlist bookmark API: generic links are not equivalent
        # to VK Video's saved playlists and are deliberately not written.
        return kinds

    def read(self, kinds):
        out = []
        calls = []
        if "video" in kinds:
            calls.append(("video", "fave.get", {"item_type": "video", "extended": 1}))
        if "channel" in kinds:
            calls.extend(("channel", "fave.getPages", {"type": t}) for t in ("groups", "users"))
        for kind, method, params in calls:
            offset = 0
            expected, seen = None, set()
            while True:
                resp = self.api(method, count=100, offset=offset, **params)
                if not isinstance(resp, dict) or not isinstance(resp.get("items"), list):
                    raise ValueError("VK вернул неполный список закладок.")
                items = resp["items"]
                total = resp.get("count")
                if not isinstance(total, int) or (expected is not None and total != expected):
                    raise ValueError("Список закладок VK изменился при загрузке. Повторите обновление.")
                expected = total
                for item in items:
                    if kind == "video":
                        value = item.get("video") or {}
                        obj = _video(value, _owner_names(resp))
                        if not obj and value.get("owner_id") is not None and value.get("id"):
                            obj = {"source": "vk", "id": "%s_%s" % (value["owner_id"], value["id"]),
                                   "title": value.get("title") or "Недоступное видео",
                                   "unavailable": "Видео недоступно этому аккаунту.", "thumb": ""}
                    elif item.get("group"):
                        obj = _group(item["group"])
                    elif item.get("user"):
                        obj = _user(item["user"])
                    else:
                        obj = None
                    if obj is None:
                        # An unknown shape is not proof that a bookmark
                        # was deleted; discard the entire read instead.
                        raise ValueError("VK вернул неизвестный вид закладки.")
                    identity = obj["id"]
                    if identity in seen:
                        raise ValueError("VK повторил страницу закладок. Повторите обновление.")
                    seen.add(identity)
                    obj["bookmarked_at"] = item.get("added_date") or item.get("updated_date") or time.time()
                    out.append((kind, obj))
                offset += len(items)
                if offset > total or (not items and offset < total):
                    raise ValueError("VK вернул неполный список закладок.")
                if offset >= resp["count"]:
                    break
        return out

    def write(self, kind, obj, add):
        if kind == "video":
            owner, vid = obj["id"].split("_")
            method = "fave.addVideo" if add else "fave.removeVideo"
            params = {"owner_id": owner, "id": vid}
        elif kind == "channel":
            owner = int(obj["id"])
            method = "fave.addPage" if add else "fave.removePage"
            params = {"group_id" if owner < 0 else "user_id": abs(owner)}
        else:
            raise ValueError("Этот тип закладок сохраняется только на устройстве.")
        if self.api(method, **params) != 1:
            raise ValueError("VK не подтвердил изменение закладки.")

    def available(self, kind, obj):
        """A missing fave may be an inaccessible object, not an unfave.
        A failed check aborts reconciliation; it never proves deletion.
        """
        if kind == "video":
            items = self.api("video.get", videos=obj["id"], extended=0).get("items") or []
            return bool(items and _video(items[0], {}) and items[0].get("can_play", 1))
        owner = int(obj["id"])
        if owner < 0:
            groups = self.api("groups.getById", group_ids=str(-owner)).get("groups") or []
            return bool(groups and not groups[0].get("deactivated") and
                        (not groups[0].get("is_closed") or groups[0].get("is_member")))
        users = self.api("users.get", user_ids=str(owner))
        return bool(users and not users[0].get("deactivated") and
                    (not users[0].get("is_closed") or users[0].get("can_access_closed")))


def create_source():
    return VKVideo()
