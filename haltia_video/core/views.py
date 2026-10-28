# SPDX-License-Identifier: GPL-2.0
"""What each page shows, for the window and the browser alike.

    Views(app).query(view, params, device) -> a copy of the view's data
    Views(app).home_sections() -> Главная's sections, in the person's order
    Views(app).actions(kind, item, context) -> what a menu offers, in order

An action is {"id", "title"} and, when a command does it, "command" and
"params"; "confirm" ({"text", "button"}) when it asks first, "undo"
({"notice", "command", "params"}) when it is done at once and offered
back. Others (open the channel, edit the order…) the adapter carries out
by id, with what the action brings (entity, order, what).

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

from .bookmarks import KINDS, SORTS, key
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
                    "bookmark_kinds": [list(k) for k in KINDS],
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
            result = {"entity": playlist, "order": order, "offset": offset,
                    "bookmarked": bool(self.app.library.bookmarks.find("playlist", playlist)),
                    "items": items, "tasks": {"done": len(tasks & done), "total": len(tasks)},
                    "has_tasks": bool(done), "has_manual": bool(self.app.library.manual_order(playlist, "custom") or
                                                                self.app.library.manual_order(playlist, "lesson")),
                    "next": (page or {}).get("next"), "loading": loading,
                    "failed": failed, "ready": page is not None, "resume": saved, "continuation": continuation}
            result["play"] = self.resume(result)
            return result
        if view == "actions":
            return {"actions": self.actions(p["kind"], p["item"], dict(p.get("context") or {}, device=device))}
        raise ValueError("Неизвестный запрос.")

    def _sync_state(self, sid):
        s = self.app.bookmark_sync.sessions.get(sid)
        if not s:
            return None
        return {"note": s["note"], "kinds": s["kinds"], "enabled": self.app.library.bookmarks.enabled(s["aid"])}

    # --- actions ----------------------------------------------------------
    def actions(self, kind, item, context=None):
        """What ⋮ (or a long press) offers on a video, a playlist page, a
        bookmark, a channel, or Закладки itself (context: its filters)."""
        app, library = self.app, self.app.library
        context = context or {}
        out = []
        if kind == "video":
            if item.get("channel_id"):
                chan = {"source": item["source"], "id": item["channel_id"],
                        "title": item.get("channel") or "Канал"}
                out.append({"id": "channel", "title": "Открыть канал «%s»" % chan["title"], "entity": chan})
                out += self._follow(chan, "Подписаться на канал", "Отписаться от канала")
            out += self._bookmark("video", item, "Убрать из закладок")
        elif kind == "channel":
            out += self._follow(item, "Подписаться", "Отписаться")
            out += self._bookmark("channel", item, "Удалить закладку")
        elif kind == "playlist":
            order = context.get("order") or library.playlist_order(item, context.get("device", "gtk"))
            saved = library.playlist_resume(item)
            out.append({"id": "edit_order", "title": "Изменить порядок",
                        "order": "lesson" if order == "lesson" else "custom", "return_order": order})
            if saved:
                out.append({"id": "locate", "title": "Показать в списке"})
            out += self._bookmark("playlist", item, "Убрать из закладок")
            if saved:
                out.append({"id": "reset", "what": "progress", "title": "Сбросить прогресс"})
            if order == "lesson" and library.task_done(item):
                out.append({"id": "reset", "what": "tasks", "title": "Сбросить выполнение"})
            if library.manual_order(item, "custom") or library.manual_order(item, "lesson"):
                out.append({"id": "reset", "what": "order", "title": "Сбросить ручной порядок"})
        elif kind == "bookmark":
            saved = library.bookmarks.get(item["key"])
            if saved is None or saved.get("deleted"):
                return []
            ident, pinned = saved["key"], "pin" in saved
            out.append({"id": "pin", "title": "Открепить" if pinned else "Закрепить",
                        "command": "bookmark_pin", "params": {"key": ident, "on": not pinned}})
            if pinned:
                out += [{"id": "up", "title": "Переместить выше", "command": "bookmark_move",
                         "params": {"key": ident, "step": -1}},
                        {"id": "down", "title": "Переместить ниже", "command": "bookmark_move",
                         "params": {"key": ident, "step": 1}}]
            link = app.bookmark_sync.link_for(saved["kind"], saved["object"])
            if link and link[0] not in saved["links"]:
                out.append({"id": "link", "title": "Синхронизировать с VK", "command": "bookmark_link",
                            "params": {"key": ident}})
            if saved["links"]:
                out.append(self._removal(saved, "Оставить только на устройстве", local_only=True))
            out.append(self._removal(saved, "Удалить закладку"))
        elif kind == "bookmarks":
            f = context
            mark = lambda on, title: ("✓ " if on else "") + title
            out += [{"id": "filter", "field": "sort", "value": v,
                     "title": mark(f.get("sort", "added") == v, "Сортировать: " + t.lower())} for v, t in SORTS]
            reverse = f.get("reverse", True)
            out.append({"id": "filter", "field": "reverse", "value": not reverse,
                        "title": "Сначала старые" if reverse else "Сначала новые"})
            everything = library.bookmarks.view()
            sources = {b["object"].get("source") for b in everything}
            if len(sources) > 1:
                out += [{"id": "filter", "field": "source", "value": s.id,
                         "title": mark(f.get("source") == s.id, "Только " + s.label)}
                        for s in app.ordered() if s.id in sources]
            if any(b["links"] for b in everything):
                out += [{"id": "filter", "field": "state", "value": v, "title": mark(f.get("state") == v, t)}
                        for v, t in (("local", "Только локальные"), ("synced", "Только синхронизируемые"))]
            out += [{"id": "add_link", "title": "Добавить по ссылке"},
                    {"id": "refresh", "title": "Обновить", "command": "refresh", "params": {}}]
        else:
            raise ValueError("Неизвестный вид действий.")
        return copy.deepcopy(out)

    def _follow(self, chan, follow, unfollow):
        library = self.app.library
        if not library.is_followed(chan["source"], chan["id"]):
            return [{"id": "follow", "title": follow, "command": "follow", "params": {"entity": chan}}]
        chans = library.channels()
        at = next((i for i, c in enumerate(chans) if c["source"] == chan["source"] and c["id"] == chan["id"]), None)
        kept = chans[at] if at is not None else chan
        title = kept.get("title") or "канал"
        action = {"id": "unfollow", "title": unfollow, "command": "unfollow", "params": {"entity": kept}}
        if library.confirm:
            action["confirm"] = {"text": "«%s»" % title, "button": "Отписаться от канала"}
        else:
            # Back at its place in the list.
            action["undo"] = {"notice": "Вы отписались от «%s»" % title, "command": "follow",
                              "params": {"entity": kept, "at": at}}
        return [action]

    def _bookmark(self, kind, obj, remove):
        saved = self.app.library.bookmarks.find(kind, obj)
        if saved is None:
            return [{"id": "bookmark_add", "title": "В закладки", "command": "bookmark_add",
                     "params": {"kind": kind, "entity": obj}}]
        return [self._removal(saved, remove)]

    def _removal(self, saved, title, local_only=False):
        """A bookmark linked to an account always asks first; others ask
        unless the person turned questions off, and are offered back."""
        name = saved["object"].get("title") or "закладка"
        action = {"id": "local" if local_only else "remove", "title": title, "command": "bookmark_remove",
                  "params": {"key": saved["key"], "local_only": local_only}}
        if saved["links"]:
            action["confirm"] = {
                "text": "Закладка будет удалена из связанных аккаунтов сервиса" +
                        (", но останется на устройстве." if local_only else " и из приложения."),
                "button": "Оставить только на устройстве" if local_only else "Удалить закладку"}
        elif self.app.library.confirm:
            action["confirm"] = {"text": "«%s»" % name, "button": "Удалить закладку"}
        else:
            action["undo"] = {"notice": "Закладка «%s» удалена" % name, "command": "bookmark_add",
                              "params": {"kind": saved["kind"], "entity": saved["object"]}}
        return action

    @staticmethod
    def resume(result):
        """A playlist page's «Продолжить»: its caption, the video it goes
        on from (and where), the action («play», or «first» from the
        start), whether it can, and a note on where it goes on; None
        without a video to go on from."""
        order, saved, status = result["order"], result["resume"], result["continuation"]
        if not saved and order == "lesson" and status and status["video"]:
            saved = {"video": status["video"], "pos": None, "duration": None, "complete": False}
        if not saved:
            return None
        kind = status["kind"] if status else None
        notes = {"missing": "Последнего видео нет среди доступных, начнём с первого доступного.",
                 "tasks_done": "Все задания выполнены.",
                 "finished": "Плейлист просмотрен.", "empty": "В плейлисте нет доступных видео."}
        note = notes.get(kind, "")
        if not note and saved["complete"] and order != "lesson":
            note = "Последнее видео досмотрено, продолжим со следующего."
        caption = {"tasks_done": "Смотреть сначала", "finished": "Начать сначала",
                   "missing": "Первое доступное видео"}.get(kind, "Продолжить")
        video = status["video"] if kind in ("next", "task_next") and status["video"] else saved["video"]
        return {"caption": caption, "title": video["title"],
                "pos": saved["pos"] if video is saved["video"] else None,
                "action": "first" if kind == "tasks_done" else "play",
                "enabled": kind != "empty", "note": note}
