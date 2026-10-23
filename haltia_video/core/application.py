# SPDX-License-Identifier: GPL-2.0
"""The single application owner. Views observe events and submit commands.

Construction is explicit: sources, Library, worker/timer runtime and cache
paths come from the host. This module imports neither GTK nor the server.
"""
import copy
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import time
import secrets

from .bookmark_sync import Sync
from .bookmarks import key
from .kept import Kept
from . import playlist as ordering
from .signin import SignIn
from .playback import Sessions


class RecentInputs(OrderedDict):
    def __setitem__(self, name, value):
        super().__setitem__(name, value)
        self.move_to_end(name)
        while len(self) > 128:
            self.popitem(last=False)


class SignInClient:
    """A view may cancel only its own sign-in, not another device's poll."""
    def __init__(self, application, source, device):
        self.application, self.source, self.device = application, source, device

    @property
    def state(self):
        return self.application.signins[self.source].state

    def begin(self, how=None):
        return self.application.command("signin", {"source": self.source, "browser": how}, self.device)

    def submit(self, code):
        return self.application.command("signin", {"source": self.source, "action": "submit", "code": code}, self.device)

    def enter_code(self):
        return self.application.command("signin", {"source": self.source, "action": "enter_code"}, self.device)

    def cancel(self):
        return self.application.command("signin", {"source": self.source, "action": "cancel"}, self.device)

    def logout(self):
        return self.application.command("signin", {"source": self.source, "action": "logout"}, self.device)

    def refresh(self):
        self.application.signins[self.source].refresh()


class Application:
    def __init__(self, sources, library, runtime, feed_path=None, home_path=None, cache_load=None, cache_save=None):
        self.sources = sources
        self.library = library
        self.runtime = runtime
        self.work = runtime.work
        self.watchers = []
        self.revision = 0
        self.closed = False
        self.epoch = 0
        self.account_notes = {}
        self.refreshing = False
        self.refresh_tried = set()
        self.feed = Kept(self._load_feed, 1800, self.work, path=feed_path, load=cache_load, save=cache_save,
                         prepare=lambda _key: (self.feed_channels(),))
        self.feed.watch(self._feed_came)
        self.home = Kept(lambda sid: self.sources[sid].home(), 3600, self.work, path=home_path, load=cache_load, save=cache_save)
        self.home.watch(lambda k, e: self.emit("cache", {"kind": "home", "key": k}))
        self.searches = Kept(self._search, 600, self.work)
        self.searches.watch(lambda k, e: self.emit("cache", {"kind": "search", "key": k}))
        self.reset_channel_data()
        self.signins = {}
        self.signin_owners = {}
        for src in self.sources.values():
            if getattr(src, "login", None):
                sign = SignIn(src, run=self.work, later=runtime.later)
                sign.watch(lambda state, src=src: self._signin_changed(src, state))
                self.signins[src.id] = sign
        self.signed_in = {sid: sign.state["kind"] == "in" for sid, sign in self.signins.items()}
        self.bookmark_sync = Sync(library.bookmarks, sources, self.work,
                                  lambda: self.emit("bookmark_sync", {}))
        self.sessions = Sessions(library, self.emit)
        self.local_session = None
        self.local_video = None
        self.local_last = None
        self.confirmations = RecentInputs()
        self.play_requests = RecentInputs()
        self.library.watch(self._library_changed)

    def watch(self, fn):
        self.watchers.append(fn)
        return lambda: self.watchers.remove(fn) if fn in self.watchers else None

    def signin_clients(self, device):
        return {sid: SignInClient(self, sid, device) for sid in self.signins}

    def emit(self, what, data):
        if self.closed:
            return
        self.revision += 1
        event = {"type": what, "revision": self.revision, "data": copy.deepcopy(data)}
        for fn in list(self.watchers):
            fn(event)

    def ordered(self):
        return sorted(self.sources.values(), key=lambda src: (src.id != "youtube", src.label))

    def is_enabled(self, sid):
        return sid in self.sources and not self.library.is_disabled(sid)

    def enabled(self):
        return [src for src in self.ordered() if self.is_enabled(src.id)]

    def wanted(self, videos):
        return [v for v in videos if self.is_enabled(v["source"]) and
                (self.library.shorts or not v.get("short"))]

    def feed_channels(self):
        return [c for c in self.library.channels() if self.is_enabled(c["source"])]

    def feed_shown(self):
        got = self.feed.get("feed") if self.feed_channels() else None
        return [v for v in self.wanted((got or {}).get("videos", []))
                if self.library.is_followed(v["source"], v.get("channel_id"))]

    def _load_feed(self, _key, chans):
        def one(chan):
            try:
                return chan, self.sources[chan["source"]].channel_videos(chan)
            except Exception:
                return chan, None
        videos, latest, failed = [], [], 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for offset in range(0, len(chans), 4):
                for chan, found in pool.map(one, chans[offset:offset + 4]):
                    if found is None:
                        failed += 1
                        continue
                    videos.extend(found)
                    times = [v["published"] for v in found if v.get("published")]
                    if times:
                        latest.append([chan["source"], chan["id"], max(times)])
        if chans and failed == len(chans):
            raise OSError("no channel answered")
        videos.sort(key=lambda v: v.get("published") or 0, reverse=True)
        videos = ([v for v in videos if not v.get("short")][:48] +
                  [v for v in videos if v.get("short")][:48])
        videos.sort(key=lambda v: v.get("published") or 0, reverse=True)
        return {"videos": videos, "latest": latest}

    def _feed_came(self, key, error):
        got = self.feed.peek(key)
        if not error and got:
            self.library.note_latest({(s, i): t for s, i, t in got["latest"]})
        self.emit("cache", {"kind": "feed", "key": key})

    def _search(self, key):
        kind, sid, query = key
        src = self.sources[sid]
        return src.search_channels(query) if kind == "channels" else src.search(query)

    def reset_channel_data(self):
        self.epoch += 1
        epoch = self.epoch
        self.channel_wanted = RecentInputs()
        self.playlist_wanted = RecentInputs()
        self.channel_videos = Kept(
            lambda _key, chan: self.sources[chan["source"]].channel_videos(chan),
            900, self.work, prepare=lambda k: (copy.deepcopy(self.channel_wanted[k]),))
        self.playlists = Kept(lambda k, chan: self.sources[k[0]].playlists(chan),
                              900, self.work, prepare=lambda k: (copy.deepcopy(self.channel_wanted[k]),))
        self.playlist_pages = Kept(lambda k, p: self.sources[k[0]].playlist_videos(p, k[3]),
                                   900, self.work, prepare=lambda k: (copy.deepcopy(self.playlist_wanted[k[:3]]),))

        def collect(k, p):
            def fetch(playlist, offset):
                if self.closed or epoch != self.epoch:
                    raise OSError("account changed or application closed")
                return self.sources[k[0]].playlist_videos(playlist, offset)
            return ordering.collect(fetch, p, locations=True)
        self.playlist_all = Kept(collect, 900, self.work, max_entries=8, max_bytes=32 * 1024 * 1024,
                                 prepare=lambda k: (copy.deepcopy(self.playlist_wanted[k]),))
        for name in ("channel_videos", "playlists", "playlist_pages", "playlist_all"):
            collection = getattr(self, name)
            collection.watch(lambda k, e, name=name, c=collection: self.emit(
                "cache", {"kind": name, "key": k, "failed": bool(e)})
                if getattr(self, name) is c else None)

    def _signin_changed(self, src, state):
        if self.closed:
            return
        now = state["kind"] == "in"
        if now != self.signed_in.get(src.id):
            self.signed_in[src.id] = now
            self.home.forget(src.id)
            self.searches = Kept(self._search, 600, self.work)
            self.searches.watch(lambda k, e: self.emit("cache", {"kind": "search", "key": k}))
            self.reset_channel_data()
            self.sessions.stop_source(src.id)
            self.account_notes[src.id] = None
        self.bookmark_sync.refresh_accounts()
        self.emit("account", {"source": src.id, "state": state})

    def _library_changed(self, what):
        if what == "follow":
            self.feed.invalidate()
        self.emit("library", {"what": what})

    def set_enabled(self, sid, on):
        if self.library.set_enabled(sid, on) and on:
            self.feed.invalidate()
            self.home.invalidate(sid)

    def refresh_channels(self):
        if self.refreshing:
            return
        stale = [c for c in self.library.lacking_details() if self.is_enabled(c["source"]) and
                 (c["source"], c["id"]) not in self.refresh_tried]
        if not stale:
            return
        self.refreshing = True
        self.refresh_tried.update((c["source"], c["id"]) for c in stale)
        epoch = self.epoch

        def fetch():
            out = []
            for chan in stale:
                if self.closed or self.epoch != epoch:
                    break
                try:
                    info = self.sources[chan["source"]].find_channel(chan["id"])
                    if info and info["id"] == chan["id"]:
                        out.append(info)
                except Exception:
                    pass
            return out

        def done(infos, error):
            self.refreshing = False
            if epoch == self.epoch:
                self.library.add_details(infos or [])
        self.work(fetch, done=done)

    def import_subscriptions(self, sid):
        epoch = self.epoch
        self.account_notes[sid] = "Загружаю каналы аккаунта…"
        self.emit("account_note", {"source": sid})

        def done(chans, error):
            if epoch != self.epoch:
                return
            if error:
                note = "Не удалось загрузить каналы: нет связи?"
            else:
                new = self.library.follow_all(chans or [])
                note = "Добавлено каналов: %d." % new if new else "Все каналы аккаунта уже здесь."
            self.account_notes[sid] = note
            self.emit("account_note", {"source": sid})
        self.work(self.sources[sid].subscriptions, done=done)

    def note_local_played(self, state):
        video = state["video"] if state else None
        if video is self.local_video:
            return
        self.local_video = video
        if self.local_session:
            self.sessions.stop(self.local_session)
            self.local_session = None
        if video:
            session = self.sessions.start("gtk", video, state.get("playlist"))
            self.local_session = session["id"]

    def note_local_position(self, state):
        if state is None:
            if self.local_session:
                self.sessions.stop(self.local_session)
                self.local_session = None
            self.local_last = None
            return
        if self.local_session not in self.sessions.sessions:
            self.local_session = None
        if state["pos"] is not None and self.local_session:
            session = self.sessions.get(self.local_session)
            if (session["video"]["source"], session["video"]["id"]) == (state["video"]["source"], state["video"]["id"]):
                first = self.local_last is None or self.local_last["pos"] is None
                self.sessions.report(self.local_session, state["pos"], state["duration"],
                                     state["paused"], state.get("ended", False), force=first)
        self.local_last = dict(state)

    def start(self):
        self.bookmark_sync.refresh_accounts()

    def close(self):
        self.closed = True
        self.epoch += 1
        self.bookmark_sync.close()
        for sign in self.signins.values():
            sign.cancel()
        for ident in list(self.sessions.sessions):
            self.sessions.stop(ident)

    # All returned data crosses an adapter boundary as a copy.
    def _videos(self, videos):
        out = []
        for video in videos:
            item = copy.deepcopy(video)
            item["progress"] = self.library.progress(video)
            item["bookmarked"] = bool(self.library.bookmarks.find("video", video))
            out.append(item)
        return out

    def _source(self, entity):
        if not isinstance(entity, dict) or entity.get("source") not in self.sources:
            raise ValueError("Неизвестный источник.")
        if not isinstance(entity.get("id"), str) or not 0 < len(entity["id"]) <= 256:
            raise ValueError("Некорректный идентификатор.")
        if len(str(entity.get("title", ""))) > 2000:
            raise ValueError("Слишком длинное название.")
        return self.sources[entity["source"]]

    def _page(self, items, offset, count=30):
        return {"items": copy.deepcopy(items[offset:offset + count]),
                "offset": offset, "next": offset + count if offset + count < len(items) else None}

    def query(self, view, params=None, device="gtk"):
        params = params or {}
        offset = max(0, min(int(params.get("offset", 0)), 100000))
        result = self._query(view, params, device, offset)
        result["revision"] = self.revision
        return copy.deepcopy(result)

    def _query(self, view, p, device, offset):
        if view == "state":
            sources = []
            for src in self.ordered():
                sign = self.signins.get(src.id)
                sources.append({"id": src.id, "label": src.label,
                                "enabled": self.is_enabled(src.id),
                                "account": copy.deepcopy(sign.state) if sign else None,
                                "login": getattr(src, "login", None),
                                "hint": getattr(src, "login_hint", ""),
                                "browsers": list(src.browsers()) if hasattr(src, "browsers") else [],
                                "note": self.account_notes.get(src.id),
                                "sync": self._sync_state(src.id)})
            return {"sources": sources, "shorts": self.library.shorts,
                    "scope": self.library.scope, "sort": self.library.sort,
                    "device": device}
        if view == "channels":
            self.refresh_channels()
            query = str(p.get("query", "")).casefold()
            items = [c for c in self.library.channels() if query in c["title"].casefold()]
            return self._page(items, offset)
        if view == "history":
            return self._page(self._videos(self.library.history()), offset)
        if view == "feed":
            result = self._page(self._videos(self.feed_shown()), offset)
            result.update(loading=self.feed.is_loading("feed"), failed=self.feed.has_failed("feed"))
            return result
        if view == "home":
            sections = [{"title": "Новое в подписках", "items": self._videos(self.feed_shown()[:6])}]
            loading = self.feed.is_loading("feed")
            for src in self.enabled():
                if not hasattr(src, "home"):
                    continue
                got = self.home.get(src.id)
                loading = loading or self.home.is_loading(src.id)
                if got:
                    sections.append({"title": got["title"], "items": self._videos(self.wanted(got["videos"])[:12])})
            return {"sections": sections, "loading": loading}
        if view == "bookmarks":
            items = self.library.bookmarks.view(
                query=str(p.get("query", ""))[:256], kind=p.get("kind", "all"),
                source=p.get("source", "all"), state=p.get("state", "all"),
                sort=p.get("sort", "added"), reverse=bool(p.get("reverse", True)))
            return self._page(items, offset)
        if view == "search":
            from .search import view as search_view
            query = str(p.get("query", "")).strip()[:256]
            if not query:
                return {"sections": [], "loading": False}
            kind = "channels" if p.get("kind") == "channels" else "search"
            scope = p.get("source", "all")
            srcs = [s for s in self.enabled() if scope == "all" or s.id == scope]
            if kind == "channels":
                direct = [s for s in srcs if getattr(s, "owns", lambda q: False)(query)]
                srcs = direct or srcs
            keys = {s.id: (kind, s.id, query) for s in srcs}
            found = {sid: self.searches.get(k) for sid, k in keys.items()}
            if kind == "search":
                found = {sid: self.wanted(v) if v is not None else None for sid, v in found.items()}
            loading = {sid for sid, k in keys.items() if self.searches.is_loading(k)}
            result = search_view(kind, query, [(s.id, s.label) for s in srcs], found,
                                 loading, {sid for sid, k in keys.items() if self.searches.has_failed(k)},
                                 p.get("sort", "date"))
            for section in result["sections"]:
                if kind == "search":
                    section["items"] = self._videos(section["items"])
                section["items"] = section["items"][:60]
            result.update(loading=bool(loading), kind=kind)
            return result
        if view == "channel":
            chan = p["entity"]
            src = self._source(chan)
            k = (chan["source"], chan["id"])
            self.channel_wanted[k] = copy.deepcopy(chan)
            videos = self.channel_videos.get(k)
            albums = self.playlists.get(k) if hasattr(src, "playlists") else []
            for album in albums or []:
                album.setdefault("owner_title", chan["title"])
            result = self._page(self._videos(self.wanted(videos or [])), offset)
            result.update(entity=chan, playlists=albums or [],
                          followed=self.library.is_followed(chan["source"], chan["id"]),
                          loading=self.channel_videos.is_loading(k) or self.playlists.is_loading(k),
                          failed=self.channel_videos.has_failed(k))
            return result
        if view in ("playlist", "locate", "reset_preview"):
            playlist = p["entity"]
            src = self._source(playlist)
            if not hasattr(src, "playlist_videos"):
                raise ValueError("Источник не поддерживает плейлисты.")
            k = (playlist["source"], str(playlist.get("owner_id", "")), playlist["id"])
            self.playlist_wanted[k] = copy.deepcopy(playlist)
            order = p.get("order") or self.library.playlist_order(playlist, device)
            if order not in dict(ordering.ORDERS):
                raise ValueError("Неизвестный порядок плейлиста.")
            self.library.set_playlist_order(playlist, order, device)
            full = self.playlist_all.peek(k)
            if order != "vk" or view != "playlist":
                full = self.playlist_all.get(k)
            saved = self.library.playlist_resume(playlist)
            if view != "playlist":
                if full is None:
                    return {"loading": self.playlist_all.is_loading(k),
                            "failed": self.playlist_all.has_failed(k)}
                videos = self.wanted(full[order])
                if view == "reset_preview":
                    token = secrets.token_urlsafe(24)
                    self.confirmations[token] = {"device": device, "epoch": self.epoch,
                                                  "until": time.monotonic() + 120,
                                                  "playlist": copy.deepcopy(playlist),
                                                  "key": k, "time": self.playlist_all.entries[k]["time"]}
                    return {"token": token, "count": len(full["vk"])}
                if not saved or not videos:
                    return {"missing": True}
                target = next((v for v in videos if key("video", v) == key("video", saved["video"])), videos[0])
                if order == "vk":
                    pos, previous = full["_locations"][(target["source"], target["id"])]
                else:
                    pos = (videos.index(target) // 30) * 30
                    previous = tuple(range(0, pos, 30))
                return {"offset": pos, "target": target["id"], "video": target,
                        "previous": previous, "missing": key("video", target) != key("video", saved["video"])}
            if order == "vk":
                page = (full or {}).get("_pages", {}).get(offset)
                if page is None:
                    page = self.playlist_pages.get(k + (offset,))
                loading = self.playlist_pages.is_loading(k + (offset,))
                failed = self.playlist_pages.has_failed(k + (offset,))
            else:
                page = ordering.page(self.wanted(full[order]), offset) if full else None
                loading = self.playlist_all.is_loading(k)
                failed = self.playlist_all.has_failed(k)
            continuation = ordering.continuation(self.wanted(full[order]), saved) if full and saved else None
            return {"entity": playlist, "order": order, "offset": offset,
                    "items": self._videos(self.wanted((page or {}).get("videos", []))),
                    "next": (page or {}).get("next"), "loading": loading,
                    "failed": failed, "ready": page is not None, "resume": saved, "continuation": continuation}
        raise ValueError("Неизвестный запрос.")

    def _sync_state(self, sid):
        s = self.bookmark_sync.sessions.get(sid)
        if not s:
            return None
        return {"note": s["note"], "kinds": s["kinds"], "enabled": self.library.bookmarks.enabled(s["aid"])}

    def command(self, name, p=None, device="gtk"):
        p = p or {}
        if name == "follow":
            obj = p["entity"]
            self._source(obj)
            self.library.follow(obj)
        elif name == "unfollow":
            obj = p["entity"]
            self._source(obj)
            self.library.unfollow(obj["source"], obj["id"])
        elif name == "bookmark_add":
            obj, kind = p["entity"], p["kind"]
            self._source(obj)
            if kind not in ("video", "playlist", "channel"):
                raise ValueError("Неизвестный тип закладки.")
            self.library.bookmarks.add(kind, obj, self.bookmark_sync.link_for(kind, obj))
            self.bookmark_sync.refresh()
        elif name == "bookmark_remove":
            self.library.bookmarks.remove(p["key"], bool(p.get("local_only")))
            self.bookmark_sync.refresh()
        elif name == "bookmark_pin":
            self.library.bookmarks.pin(p["key"], bool(p["on"]))
        elif name == "bookmark_move":
            self.library.bookmarks.move(p["key"], -1 if p["step"] < 0 else 1)
        elif name == "bookmark_link":
            b = self.library.bookmarks.get(p["key"])
            link = self.bookmark_sync.link_for(b["kind"], b["object"]) if b else None
            if not link:
                raise ValueError("Синхронизация недоступна.")
            self.library.bookmarks.link(b["key"], *link)
            self.bookmark_sync.refresh()
        elif name == "shorts":
            self.library.set_shorts(bool(p["on"]))
        elif name == "scope":
            if p["source"] not in self.sources and p["source"] != "all":
                raise ValueError("Неизвестный источник.")
            self.library.set_scope(p["source"])
        elif name == "sort":
            self.library.set_sort(p["sort"])
        elif name == "source_enabled":
            if p["source"] not in self.sources:
                raise ValueError("Неизвестный источник.")
            self.set_enabled(p["source"], bool(p["on"]))
        elif name == "import_subscriptions":
            self.import_subscriptions(p["source"])
        elif name == "signin":
            sign = self.signins[p["source"]]
            action = p.get("action", "begin")
            if action == "begin":
                self.signin_owners[p["source"]] = device
                sign.begin(p.get("browser"))
            elif action == "submit":
                sign.submit(str(p.get("code", ""))[:64])
            elif action == "enter_code":
                sign.enter_code()
            elif action == "logout":
                sign.logout()
            elif action == "cancel":
                if self.signin_owners.get(p["source"]) == device:
                    sign.cancel()
            else:
                raise ValueError("Неизвестное действие входа.")
        elif name == "refresh":
            self.bookmark_sync.refresh_accounts()
            self.bookmark_sync.refresh()
            self.feed.invalidate()
        elif name == "sync_enabled":
            self.bookmark_sync.enable(p["source"], bool(p["on"]))
        elif name == "reset_progress":
            preview = self.confirmations.pop(p.get("token"), None)
            if (not preview or preview["device"] != device or preview["epoch"] != self.epoch or
                    preview["until"] < time.monotonic()):
                raise ValueError("Подтверждение устарело. Повторите сброс.")
            entry = self.playlist_all.entries.get(preview["key"])
            if not entry or entry["time"] != preview["time"]:
                raise ValueError("Список изменился. Подтвердите сброс ещё раз.")
            self.reset_progress(preview["playlist"], entry["value"]["vk"])
        else:
            raise ValueError("Неизвестная команда.")
        return {"ok": True, "revision": self.revision}

    def playback_plan(self, video=None, playlist=None, order=None, resume=False,
                      start_over=False, device="gtk"):
        """One queue/continuation rule for local and browser adapters.
        A pending collection is represented by None, never a partial queue.
        """
        queue = ()
        index = None
        if playlist:
            self._source(playlist)
            k = (playlist["source"], str(playlist.get("owner_id", "")), playlist["id"])
            self.playlist_wanted[k] = copy.deepcopy(playlist)
            orders = self.playlist_all.get(k)
            if orders is None:
                if self.playlist_all.has_failed(k) and not self.playlist_all.is_loading(k):
                    raise ValueError("Не удалось загрузить плейлист. Повторите при подключении.")
                return None
            order = order or self.library.playlist_order(playlist, device)
            if order not in ("vk", "reverse", "lesson"):
                raise ValueError("Неизвестный порядок.")
            queue = self.wanted(orders[order])
            if resume:
                saved = self.library.playlist_resume(playlist)
                if not saved:
                    raise ValueError("Нет сохранённого прогресса.")
                result = ordering.continuation(queue, saved)
                video = result["video"]
                start_over = result["kind"] == "finished"
            else:
                video = next((v for v in queue if video and key("video", v) == key("video", video)), None)
            if not video:
                raise ValueError("Этого видео нет среди доступных видео плейлиста.")
            index = next(i for i, v in enumerate(queue) if v is video)
        self._source(video)
        return {"video": video, "queue": queue, "index": index, "playlist": playlist,
                "start_over": start_over, "order": order}

    def reset_progress(self, playlist, videos):
        ids = {key("video", v) for v in videos}
        saved = self.library.playlist_resume(playlist)
        if saved:
            ids.add(key("video", saved["video"]))
        for ident, s in list(self.sessions.sessions.items()):
            if key("video", s["video"]) in ids or (s["playlist"] and key("playlist", s["playlist"]) == key("playlist", playlist)):
                self.sessions.stop(ident)
        self.library.reset_playlist_progress(playlist, videos)
