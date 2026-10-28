# SPDX-License-Identifier: GPL-2.0
"""What each page shows, for the window and the browser alike.

    Views(app).query(view, params, device) -> a copy of the view's data
    Views(app).home_sections() -> Главная's sections, in the person's order

The window (GTK) and the browser draw the same pages: what a page holds,
in which sections, what a card says about itself (watched, bookmarked,
its channel followed) and where «Все →» goes is decided here once, so
the two cannot drift apart. How it is drawn (columns, gestures, lists
made as scrolled to) is each adapter's own. Reads the Application's
Library and caches; changes nothing but the caches' wishes (what to
fetch) and a preview's confirmation token.
"""
import copy
import secrets
import time

from .bookmarks import key
from . import playlist as ordering

# What a section of Главная brings: a window shows one row of it.
HOME_ITEMS = 12


class Views:
    def __init__(self, app):
        self.app = app

    # All returned data crosses an adapter boundary as a copy.
    def _videos(self, videos):
        out = []
        for video in videos:
            item = copy.deepcopy(video)
            item["progress"] = self.app.library.progress(video)
            item["bookmarked"] = bool(self.app.library.bookmarks.find("video", video))
            if video.get("channel_id"):
                item["followed"] = self.app.library.is_followed(video["source"], video["channel_id"])
            out.append(item)
        return out

    def _cursor(self, cursor):
        """A playlist begun, as Главная and «Мои плейлисты» show it: where it
        stopped (with its position) and the channel it opens with."""
        playlist, video = cursor["playlist"], cursor["video"]
        item = self._videos([video])[0]
        item["pos"] = (self.app.library.position(video) or {}).get("pos")
        chan = {"source": playlist["source"], "id": str(playlist.get("owner_id", "")),
                "title": playlist.get("owner_title") or video.get("channel") or "", "from_tab": True}
        return {"playlist": playlist, "video": item, "chan": chan,
                "bookmarked": bool(self.app.library.bookmarks.find("playlist", playlist))}

    def home_sections(self):
        """Главная's sections, as {"id", "title", "on"}, in the person's order."""
        titles = {"continue": "Продолжить просмотр", "playlists": "Мои плейлисты",
                  "feed": "Новое в подписках", "bookmarks": "Закладки"}
        for src in self.app.ordered():
            got = self.app.home.get(src.id) if hasattr(src, "home") else None
            titles["source:" + src.id] = (got or {}).get("title") or src.label
        return [{"id": sid, "title": titles[sid], "on": on}
                for sid, on in self.app.library.home_sections([s.id for s in self.app.ordered()])]

    def _home(self):
        """Главная: each section switched on, with what it shows (up to
        HOME_ITEMS; a window shows one row of them) and where «Все →» goes
        ("more"; "always": also when the row holds them all)."""
        enabled = {s.id for s in self.app.enabled()}
        sections, loading = [], False
        for entry in self.home_sections():
            sid, title = entry["id"], entry["title"]
            if not entry["on"] or (sid.startswith("source:") and sid[7:] not in enabled):
                continue
            section = {"id": sid, "title": title, "kind": "videos", "items": [], "more": None,
                       "always": False, "hint": ""}
            if sid == "continue":
                section.update(items=self._videos(self.app.wanted(self.app.library.unfinished())),
                               more={"view": "history"}, always=True)
            elif sid == "playlists":
                section.update(kind="playlists", more={"view": "my_playlists"},
                               items=[self._cursor(c) for c in self.app.library.playlists_in_progress()])
            elif sid == "feed":
                shown = self.app.feed_shown()
                loading = loading or self.app.feed.is_loading("feed")
                if not self.app.library.channels():
                    section["hint"] = ("Чтобы подписаться на канал, нажмите ⋮ на его видео "
                                       "или найдите канал на странице «Каналы».")
                elif shown:
                    section.update(items=self._videos(shown), more={"view": "feed"}, always=True)
                else:
                    section["hint"] = "Загружаю…" if self.app.feed.is_loading("feed") else "Пока ничего нет."
            elif sid == "bookmarks":
                section.update(items=self._videos([b["object"] for b in self.app.library.bookmarks.view(kind="video")]),
                               more={"view": "bookmarks"}, always=True)
            else:
                src = self.app.sources[sid[7:]]
                got = self.app.home.get(src.id) if hasattr(src, "home") else None
                loading = loading or (hasattr(src, "home") and self.app.home.is_loading(src.id))
                videos = self.app.wanted((got or {}).get("videos") or [])
                if videos:
                    section.update(items=self._videos(videos), more={"view": "source_home", "source": src.id})
                elif getattr(src, "login", None) == "cookies" and not src.account():
                    section["hint"] = ("Рекомендации %s появятся после входа в аккаунт "
                                       "в настройках (⚙)." % src.label)
                    section["title"] = src.label
            if not section["items"] and not section["hint"]:
                continue
            section["total"] = len(section["items"])
            section["items"] = section["items"][:HOME_ITEMS]
            sections.append(section)
        return {"sections": sections, "loading": loading}

    def _page(self, items, offset, count=30):
        return {"items": copy.deepcopy(items[offset:offset + count]),
                "offset": offset, "next": offset + count if offset + count < len(items) else None}

    def query(self, view, params=None, device="gtk"):
        params = params or {}
        offset = max(0, min(int(params.get("offset", 0)), 100000))
        result = self._query(view, params, device, offset)
        result["revision"] = self.app.revision
        return copy.deepcopy(result)

    def _query(self, view, p, device, offset):
        # A page of a list, or (a browser drawing a list again as far as it
        # had scrolled) more of it at once.
        count = max(1, min(int(p.get("count", 30)), 1000))
        if view == "state":
            sources = []
            for src in self.app.ordered():
                sign = self.app.signins.get(src.id)
                sources.append({"id": src.id, "label": src.label,
                                "enabled": self.app.is_enabled(src.id),
                                "account": copy.deepcopy(sign.state) if sign else None,
                                "login": getattr(src, "login", None),
                                "hint": getattr(src, "login_hint", ""),
                                "browsers": list(src.browsers()) if hasattr(src, "browsers") else [],
                                "note": self.app.account_notes.get(src.id),
                                "sync": self._sync_state(src.id)})
            return {"sources": sources, "shorts": self.app.library.shorts,
                    "scope": self.app.library.scope, "sort": self.app.library.sort,
                    "confirm": self.app.library.confirm, "home_sections": self.home_sections(),
                    "device": device}
        if view == "channels":
            self.app.refresh_channels()
            query = str(p.get("query", "")).casefold()
            items = [dict(c, bookmarked=bool(self.app.library.bookmarks.find("channel", c)))
                     for c in self.app.library.channels() if query in c["title"].casefold()]
            return self._page(items, offset, count)
        if view == "history":
            return self._page(self._videos(self.app.library.history()), offset, count)
        if view == "feed":
            result = self._page(self._videos(self.app.feed_shown()), offset, count)
            result.update(loading=self.app.feed.is_loading("feed"), failed=self.app.feed.has_failed("feed"))
            return result
        if view == "home":
            return self._home()
        if view == "my_playlists":
            return self._page([self._cursor(c) for c in self.app.library.playlists_in_progress()], offset, count)
        if view == "source_home":
            src = self.app.sources.get(p.get("source"))
            if src is None or not hasattr(src, "home"):
                raise ValueError("Неизвестный источник.")
            got = self.app.home.get(src.id) or {}
            result = self._page(self._videos(self.app.wanted(got.get("videos") or [])), offset, count)
            result.update(title=got.get("title") or src.label, loading=self.app.home.is_loading(src.id))
            return result
        if view == "bookmarks":
            items = self.app.library.bookmarks.view(
                query=str(p.get("query", ""))[:256], kind=p.get("kind", "all"),
                source=p.get("source", "all"), state=p.get("state", "all"),
                sort=p.get("sort", "added"), reverse=bool(p.get("reverse", True)))
            return self._page(items, offset, count)
        if view == "search":
            from .search import view as search_view
            query = str(p.get("query", "")).strip()[:256]
            if not query:
                return {"sections": [], "loading": False}
            kind = "channels" if p.get("kind") == "channels" else "search"
            scope = p.get("source", "all")
            srcs = [s for s in self.app.enabled() if scope == "all" or s.id == scope]
            if kind == "channels":
                direct = [s for s in srcs if getattr(s, "owns", lambda q: False)(query)]
                srcs = direct or srcs
            keys = {s.id: (kind, s.id, query) for s in srcs}
            found = {sid: self.app.searches.get(k) for sid, k in keys.items()}
            if kind == "search":
                found = {sid: self.app.wanted(v) if v is not None else None for sid, v in found.items()}
            loading = {sid for sid, k in keys.items() if self.app.searches.is_loading(k)}
            result = search_view(kind, query, [(s.id, s.label) for s in srcs], found,
                                 loading, {sid for sid, k in keys.items() if self.app.searches.has_failed(k)},
                                 p.get("sort", "date"))
            for section in result["sections"]:
                if kind == "search":
                    section["items"] = self._videos(section["items"])
                else:
                    section["items"] = [dict(c, followed=self.app.library.is_followed(c["source"], c["id"]),
                                             bookmarked=bool(self.app.library.bookmarks.find("channel", c)))
                                        for c in section["items"]]
                section["items"] = section["items"][:60]
            result.update(loading=bool(loading), kind=kind)
            return result
        if view == "channel":
            chan = p["entity"]
            src = self.app._source(chan)
            k = (chan["source"], chan["id"])
            self.app.channel_wanted[k] = copy.deepcopy(chan)
            videos = self.app.channel_videos.get(k)
            albums = self.app.playlists.get(k) if hasattr(src, "playlists") else []
            for album in albums or []:
                album.setdefault("owner_title", chan["title"])
            result = self._page(self._videos(self.app.wanted(videos or [])), offset, count)
            # What is known of a followed channel (its avatar, subscribers…).
            known = next((c for c in self.app.library.channels()
                          if c["source"] == chan["source"] and c["id"] == chan["id"]), chan)
            albums = [dict(a, bookmarked=bool(self.app.library.bookmarks.find("playlist", a))) for a in albums or []]
            result.update(entity=chan, known=known, playlists=albums,
                          bookmarked=bool(self.app.library.bookmarks.find("channel", chan)),
                          followed=self.app.library.is_followed(chan["source"], chan["id"]),
                          loading=self.app.channel_videos.is_loading(k) or self.app.playlists.is_loading(k),
                          failed=self.app.channel_videos.has_failed(k))
            return result
        if view in ("playlist", "locate", "reset_preview", "playlist_editor"):
            playlist = p["entity"]
            src = self.app._source(playlist)
            if not hasattr(src, "playlist_videos"):
                raise ValueError("Источник не поддерживает плейлисты.")
            k = (playlist["source"], str(playlist.get("owner_id", "")), playlist["id"])
            self.app.playlist_wanted[k] = copy.deepcopy(playlist)
            order = p.get("order") or self.app.library.playlist_order(playlist, device)
            if order not in dict(ordering.ORDERS):
                raise ValueError("Неизвестный порядок плейлиста.")
            self.app.library.set_playlist_order(playlist, order, device)
            full = self.app.playlist_all.peek(k)
            if order != "vk" or view != "playlist":
                full = self.app.playlist_all.get(k)
            saved = self.app.library.playlist_resume(playlist)
            if view == "playlist_editor":
                if order not in ("lesson", "custom"):
                    raise ValueError("Для редактирования выберите «Мой порядок» или номера уроков.")
                if full is None:
                    return {"loading": self.app.playlist_all.is_loading(k), "failed": self.app.playlist_all.has_failed(k)}
                token = p.get("token") or self.app.editor.begin(device, playlist, order, full,
                                                            self.app.wanted(self.app.playlist_ordered(playlist, full, order)), self.app.epoch)
                draft = self.app.editor.get(token, device, full, self.app.epoch)
                if key("playlist", draft["playlist"]) != key("playlist", playlist) or order != draft["order"]:
                    raise ValueError("Черновик другого плейлиста.")
                entries = full["_entries"]
                ids = draft["ids"]
                # The editor shows the whole playlist (it builds the rows lazily).
                result = self._page([entries[i] for i in ids], offset,
                                    max(1, min(int(p.get("count", 30)), 2000)))
                result.update(token=token, order=order, total=len(ids))
                for i, video in enumerate(result["items"], offset):
                    video.update(position=i, minimum=0, maximum=len(ids) - 1)
                    if order == "lesson":
                        lo, hi = draft["bounds"][i]
                        video.update(minimum=lo, maximum=hi)
                return result
            if view != "playlist":
                if full is None:
                    return {"loading": self.app.playlist_all.is_loading(k),
                            "failed": self.app.playlist_all.has_failed(k)}
                videos = self.app.wanted(self.app.playlist_ordered(playlist, full, order))
                if view == "reset_preview":
                    token = secrets.token_urlsafe(24)
                    self.app.confirmations[token] = {"device": device, "epoch": self.app.epoch,
                                                  "until": time.monotonic() + 120,
                                                  "playlist": copy.deepcopy(playlist),
                                                  "what": p.get("what", "progress"),
                                                  "key": k, "time": self.app.playlist_all.entries[k]["time"]}
                    return {"token": token, "count": len(full["vk"])}
                if not saved or not videos:
                    return {"missing": True}
                target = next((v for v in videos if ordering.same_entry(v, saved["video"])), videos[0])
                if order == "vk":
                    pos, previous = full["_entry_locations"][target["entry_id"]]
                else:
                    pos = (videos.index(target) // 30) * 30
                    previous = tuple(range(0, pos, 30))
                return {"offset": pos, "target": target["id"], "video": target,
                        "entry_id": target["entry_id"],
                        "previous": previous, "missing": key("video", target) != key("video", saved["video"])}
            if order == "vk":
                page = (full or {}).get("_pages", {}).get(offset)
                if page is None:
                    page = self.app.playlist_pages.get(k + (offset,))
                    if page is not None:
                        page = {"videos": [dict(v, source_offset=offset + i) for i, v in enumerate(page["videos"])],
                                "next": page["next"]}
                loading = self.app.playlist_pages.is_loading(k + (offset,))
                failed = self.app.playlist_pages.has_failed(k + (offset,))
            else:
                page = ordering.page(self.app.wanted(self.app.playlist_ordered(playlist, full, order)), offset) if full else None
                loading = self.app.playlist_all.is_loading(k)
                failed = self.app.playlist_all.has_failed(k)
            videos = self.app.wanted(self.app.playlist_ordered(playlist, full, order)) if full else []
            done = self.app.library.task_done(playlist)
            continuation = (ordering.task_continuation(videos, saved, done) if order == "lesson" and full else
                            ordering.continuation(videos, saved) if full and saved else None)
            items = self._videos(self.app.wanted((page or {}).get("videos", [])))
            if order == "lesson":
                for video in items:
                    video["task_done"] = key("video", video) in done
            tasks = {key("video", v) for v in videos}
            return {"entity": playlist, "order": order, "offset": offset,
                    "bookmarked": bool(self.app.library.bookmarks.find("playlist", playlist)),
                    "items": items, "tasks": {"done": len(tasks & done), "total": len(tasks)},
                    "has_tasks": bool(done), "has_manual": bool(self.app.library.manual_order(playlist, "custom") or
                                                                self.app.library.manual_order(playlist, "lesson")),
                    "next": (page or {}).get("next"), "loading": loading,
                    "failed": failed, "ready": page is not None, "resume": saved, "continuation": continuation}
        raise ValueError("Неизвестный запрос.")

    def _sync_state(self, sid):
        s = self.app.bookmark_sync.sessions.get(sid)
        if not s:
            return None
        return {"note": s["note"], "kinds": s["kinds"], "enabled": self.app.library.bookmarks.enabled(s["aid"])}
