# SPDX-License-Identifier: GPL-2.0
"""The Library owns followed channels, bookmarks, history and settings in
video.json and history.json. playback.json keeps video positions and
playlist cursors independently of history's display limit.

Callers learn about changes through watch(fn): fn(what) with what one of
"follow" (channels added), "unfollow", "details" (what is known about
followed channels changed), "history", "position" (how far a video in
the history was watched), "bookmarks", "playlist_progress",
"playlist_order" and "settings". Not thread-safe: every call comes from
the application's owner (workers hand results back there).
"""
import time

from .bookmarks import Bookmarks, key as bookmark_key
from .playlist import ORDERS


HISTORY_MAX = 100
# Positions outlive the history, but not forever: playback.json is written
# whole at every pause and half minute, and the tablet's flash is slow.
# The least recently played go first; a course of lessons fits many times.
PLAYBACK_MAX = 1000
# A followed channel's avatar, subscribers and description are asked for
# again after this long; a channel followed from a card has none at all.
DETAILS_MAX_AGE = 7 * 86400
# Where a video goes on from: not the first seconds (start over), and not
# its end (watched: start over next time, shown as seen whole).
RESUME_MIN = 10
DONE_TAIL = 10
SCOPES_ALL = "all"
SORTS = ("date", "relevance")
PLAYLIST_ORDERS = tuple(oid for oid, _title in ORDERS)


def _same(a, source, cid):
    return a["source"] == source and a["id"] == cid


def _done(pos, duration):
    return bool(duration) and pos > 0 and duration - pos <= DONE_TAIL



# What the core's views add to an entity for showing it (core/views.py),
# never kept with it: a history entry, a bookmark or a follow made from
# what a page showed takes the entity as the service gave it.
VIEW_FIELDS = ("progress", "bookmarked", "followed", "pos", "task_done", "label", "hide_channel")


def plain(entity):
    return {k: v for k, v in entity.items() if k not in VIEW_FIELDS} if isinstance(entity, dict) else entity

class Library:
    def __init__(self, store):
        self.store = store
        cfg = store.load("config") or {}
        cfg.setdefault("channels", [])
        cfg.setdefault("disabled", [])
        cfg.setdefault("scope", SCOPES_ALL)
        cfg.setdefault("sort", SORTS[0])  # search results: newest first
        cfg.pop("source", None)  # the single search source of 1.2
        self._cfg = cfg
        self._history = self.store.load("history") or []
        self._playback = self.store.load("playback") or {}
        self._playback.setdefault("videos", {})
        self._playback.setdefault("playlists", {})
        self._playback.setdefault("orders", {})
        self._playback.setdefault("playlist_edits", {})
        self._playback.setdefault("tasks", {})
        # Import existing positions once; future history eviction must
        # not forget a lesson's position or a playlist's last video.
        for video in self._history:
            self._playback["videos"].setdefault(bookmark_key("video", video), dict(video))
        self._seen = None  # (source, id): its history entry, made when asked
        self._watchers = []
        self.bookmarks = Bookmarks(cfg.get("bookmarks", {}), self._bookmarks_changed)

    def _bookmarks_changed(self):
        self._cfg["bookmarks"] = self.bookmarks.data
        self._changed("bookmarks")

    def watch(self, fn):
        self._watchers.append(fn)

    def _changed(self, what):
        if what == "history":
            self._seen = None  # a new list; a position only changes an entry
        if what in ("history", "position"):
            self.store.save("history", self._history)
            self.store.save("playback", self._playback)
        elif what in ("playlist_progress", "playlist_order", "playlist_edits", "tasks"):
            self.store.save("playback", self._playback)
        else:
            self.store.save("config", self._cfg)
        for fn in list(self._watchers):
            fn(what)

    # --- followed channels ------------------------------------------------
    def channels(self):
        return list(self._cfg["channels"])

    def is_followed(self, source, cid):
        return any(_same(c, source, cid) for c in self._cfg["channels"])

    def follow(self, chan, at=None):
        """Follow a channel (again: its entry is replaced). True if new.
        at puts it back at that place in the list (an unfollow undone)."""
        return self.follow_all([chan], at=at) == 1

    def follow_all(self, chans, now=None, at=None, account=False):
        """Follow several channels at once (an account's); the number that
        were not followed before. account: from an account's subscriptions,
        which pass over the channels the person unfollowed here."""
        now = now or time.time()
        new = 0
        kept = self._cfg["channels"]
        dropped = set(self._cfg.get("dropped", []))
        if account:
            chans = [c for c in chans if "%s|%s" % (c["source"], c["id"]) not in dropped]
        elif dropped & {"%s|%s" % (c["source"], c["id"]) for c in chans}:
            # Followed again by hand: an account may bring it back later.
            self._cfg["dropped"] = sorted(dropped - {"%s|%s" % (c["source"], c["id"]) for c in chans})
        for chan in chans:
            if not self.is_followed(chan["source"], chan["id"]):
                new += 1
            kept = [c for c in kept if not _same(c, chan["source"], chan["id"])]
            chan = dict(chan)
            if chan.get("thumb") or chan.get("subscribers") is not None:
                chan["updated"] = now  # found with its details
            if at is None:
                kept.append(chan)
            else:
                kept.insert(max(int(at), 0), chan)
        self._cfg["channels"] = kept
        if chans:
            self._changed("follow")
        return new

    def unfollow(self, source, cid):
        """Unfollow; an account's sync will not bring the channel back."""
        before = len(self._cfg["channels"])
        self._cfg["channels"] = [c for c in self._cfg["channels"] if not _same(c, source, cid)]
        if len(self._cfg["channels"]) != before:
            dropped = set(self._cfg.get("dropped", [])) | {"%s|%s" % (source, cid)}
            self._cfg["dropped"] = sorted(dropped)
            self._changed("unfollow")

    def synced(self, source_id):
        """When the account's subscriptions were last brought here (0: never)."""
        return self._cfg.get("synced", {}).get(source_id, 0)

    def note_synced(self, source_id, now=None):
        self._cfg.setdefault("synced", {})[source_id] = now or time.time()
        self._changed("settings")

    def lacking_details(self, now=None):
        """Followed channels to ask about again: known only by name, or
        their details a week old."""
        now = now or time.time()
        return [c for c in self._cfg["channels"]
                if "updated" not in c or now - c["updated"] > DETAILS_MAX_AGE]

    def add_details(self, infos, now=None):
        """What a source told about followed channels (find_channel)."""
        now = now or time.time()
        changed = False
        for info in infos:
            for chan in self._cfg["channels"]:
                if _same(chan, info["source"], info["id"]):
                    chan.update(info, updated=now)
                    changed = True
        if changed:
            self._changed("details")

    def note_latest(self, latest):
        """The time of each channel's newest video, {(source, id): time},
        as the feed found it; Каналы tells it before the feed loads."""
        changed = False
        for chan in self._cfg["channels"]:
            when = latest.get((chan["source"], chan["id"]))
            if when and when != chan.get("latest"):
                chan["latest"] = when
                changed = True
        if changed:
            self._changed("details")

    # --- history ----------------------------------------------------------
    def history(self):
        return list(self._history)

    def unfinished(self):
        """Videos of the history begun and not watched to the end."""
        return [v for v in self._history if self.progress(v) and not self.completed(v)]

    def played(self, video):
        """Put a video first in the history, once (keeping how far it was
        watched)."""
        old = self.position(video)
        entry = plain(video)
        for key in ("pos", "duration", "ended"):
            if old and key in old:
                entry[key] = old[key]
        videos = self._playback["videos"]
        ident = bookmark_key("video", video)
        videos.pop(ident, None)  # last in the dict = most recently played
        videos[ident] = dict(entry)
        for old_ident in list(videos)[:max(0, len(videos) - PLAYBACK_MAX)]:
            del videos[old_ident]
        self._history = [entry] + [v for v in self._history
                                  if not _same(v, video["source"], video["id"])]
        del self._history[HISTORY_MAX:]
        self._changed("history")

    def _entry(self, video):
        if self._seen is None:
            self._seen = {(v["source"], v["id"]): v for v in self._history}
        return self._seen.get((video["source"], video["id"]))

    def note_position(self, video, pos, duration, ended=False):
        """How far a played video was watched, even after history eviction."""
        entry = self.position(video)
        if entry is None or pos is None:
            return
        if (entry.get("pos"), entry.get("duration"), bool(entry.get("ended"))) != (pos, duration, ended):
            entry.update(pos=pos, duration=duration, ended=ended)
            history = self._entry(video)
            if history:
                history.update(pos=pos, duration=duration, ended=ended)
            self._changed("position")

    def position(self, video):
        return self._playback["videos"].get(bookmark_key("video", video))

    def completed(self, video):
        entry = self.position(video)
        return bool(entry and (entry.get("ended") or
                              _done(entry.get("pos") or 0, entry.get("duration"))))

    def playlist_order(self, playlist, device="gtk"):
        orders = (self._playback["orders"] if device == "gtk" else
                  self._playback.get("device_orders", {}).get(device, {}))
        # An order a newer version saved falls back to the service's, and
        # stays in the file for that version.
        order = orders.get(bookmark_key("playlist", playlist))
        return order if order in PLAYLIST_ORDERS else "vk"

    def set_playlist_order(self, playlist, order, device="gtk"):
        if order not in PLAYLIST_ORDERS:
            raise ValueError(order)
        if order != self.playlist_order(playlist, device):
            orders = (self._playback["orders"] if device == "gtk" else
                      self._playback.setdefault("device_orders", {}).setdefault(device, {}))
            orders[bookmark_key("playlist", playlist)] = order
            self._changed("playlist_order")

    def note_playlist(self, playlist, video):
        ident = bookmark_key("playlist", playlist)
        old = self._playback["playlists"].get(ident)
        if (not old or not _same(old["video"], video["source"], video["id"]) or
                old["video"].get("entry_id") != video.get("entry_id")):
            self._playback["playlists"][ident] = {"playlist": plain(playlist), "video": plain(video),
                                                  "at": time.time()}
            self._changed("playlist_progress")

    def playlists_in_progress(self):
        """The playlists with a video to go on from, the latest first."""
        cursors = list(self._playback["playlists"].values())
        cursors.reverse()  # 2.8 and before kept no time: the dict's order
        return sorted(cursors, key=lambda c: -c.get("at", 0))

    def playlist_resume(self, playlist):
        cursor = self._playback["playlists"].get(bookmark_key("playlist", playlist))
        if not cursor:
            return None
        video = cursor["video"]
        pos = self.position(video) or {}
        return {"video": video, "pos": pos.get("pos"), "duration": pos.get("duration"),
                "complete": self.completed(video)}

    def reset_playlist_progress(self, playlist, videos):
        """Forget this cursor and the shared positions of its videos.
        Keep history entries and metadata; clear both copies of progress
        so migration cannot bring a reset position back on restart.
        """
        ident = bookmark_key("playlist", playlist)
        cursor = self._playback["playlists"].pop(ident, None)
        videos = list(videos) + ([cursor["video"]] if cursor else [])
        ids = {bookmark_key("video", v) for v in videos}
        for entry in list(self._playback["videos"].values()) + self._history:
            if bookmark_key("video", entry) in ids:
                entry.pop("pos", None)
                entry.pop("ended", None)
        self._changed("position")

    def resume_at(self, video):
        """Where to go on from (seconds), or None to start at the beginning."""
        entry = self.position(video)
        pos = entry and entry.get("pos")
        if not pos or pos < RESUME_MIN or self.completed(video):
            return None
        return pos

    def progress(self, video):
        """How much of it was watched, 0 to 1 (1: to the end), or None."""
        entry = self.position(video)
        pos = entry and entry.get("pos")
        duration = entry and entry.get("duration")
        if not pos or not duration:
            return None
        return 1.0 if self.completed(video) else min(pos / duration, 1.0)

    def edit_version(self, playlist):
        return self._playback["playlist_edits"].get(bookmark_key("playlist", playlist), {}).get("version", 0)

    def manual_order(self, playlist, mode):
        return list(self._playback["playlist_edits"].get(bookmark_key("playlist", playlist), {}).get(mode, []))

    def save_manual_order(self, playlist, mode, ids, version):
        if version != self.edit_version(playlist):
            raise ValueError("Порядок уже изменён на другом устройстве. Загрузите свежий вариант.")
        entry = self._playback["playlist_edits"].setdefault(bookmark_key("playlist", playlist), {})
        entry[mode] = list(ids)
        entry["version"] = version + 1
        self._changed("playlist_edits")

    def reset_manual_order(self, playlist):
        ident = bookmark_key("playlist", playlist)
        self._playback["playlist_edits"][ident] = {"version": self.edit_version(playlist) + 1}
        self._changed("playlist_edits")

    def task_done(self, playlist):
        return set(self._playback["tasks"].get(bookmark_key("playlist", playlist), []))

    def set_task(self, playlist, video, on):
        done = self.task_done(playlist)
        ident = bookmark_key("video", video)
        if (ident in done) == bool(on):
            return
        if on:
            done.add(ident)
        else:
            done.discard(ident)
        self._playback["tasks"][bookmark_key("playlist", playlist)] = sorted(done)
        self._changed("tasks")

    def reset_tasks(self, playlist):
        self._playback["tasks"].pop(bookmark_key("playlist", playlist), None)
        self._changed("tasks")

    # --- settings ---------------------------------------------------------
    def is_disabled(self, source_id):
        return source_id in self._cfg["disabled"]

    def set_enabled(self, source_id, on):
        """True if that changed anything."""
        disabled = [i for i in self._cfg["disabled"] if i != source_id]
        if not on:
            disabled.append(source_id)
        if disabled == self._cfg["disabled"]:
            return False
        self._cfg["disabled"] = disabled
        self._changed("settings")
        return True

    @property
    def shorts(self):
        """Whether Shorts are shown: not unless the person turns them on."""
        return self._cfg.get("shorts", False)

    def home_sections(self, source_ids):
        """Главная's sections in order, as (id, on): "continue", "playlists",
        "feed", "source:<id>" for each source, "bookmarks" (off unless
        turned on). A section new since the person arranged them comes last."""
        defaults = (["continue", "playlists", "feed"] +
                    ["source:" + sid for sid in source_ids] + ["bookmarks"])
        saved = [(e["id"], bool(e["on"])) for e in self._cfg.get("home", []) if e["id"] in defaults]
        known = {sid for sid, _on in saved}
        return saved + [(sid, sid != "bookmarks") for sid in defaults if sid not in known]

    def set_home_sections(self, sections):
        self._cfg["home"] = [{"id": sid, "on": bool(on)} for sid, on in sections]
        self._changed("settings")

    @property
    def confirm(self):
        """Whether an unfollow or a removed bookmark asks first; off, a note
        offers it back instead."""
        return self._cfg.get("confirm", True)

    def set_confirm(self, on):
        if bool(on) != self.confirm:
            self._cfg["confirm"] = bool(on)
            self._changed("settings")

    def layout(self, screen, default="grid"):
        """How a screen shows what it holds in the window: "grid" (cards)
        or "list" (rows); default until the person chose. The window's own:
        a browser keeps its choice in the browser, as a phone and a tablet
        may want them apart."""
        return self._cfg.get("layouts", {}).get(screen, default)

    def set_layout(self, screen, mode):
        if mode not in ("grid", "list"):
            raise ValueError("Неизвестный вид.")
        if mode != self._cfg.get("layouts", {}).get(screen):
            self._cfg.setdefault("layouts", {})[screen] = mode
            self._changed("layout")

    def set_shorts(self, on):
        if bool(on) != self.shorts:
            self._cfg["shorts"] = bool(on)
            self._changed("settings")

    @property
    def scope(self):
        """"all", or the id of the one source searches go to."""
        return self._cfg["scope"]

    def set_scope(self, scope):
        if scope != self._cfg["scope"]:
            self._cfg["scope"] = scope
            self._changed("settings")

    @property
    def sort(self):
        """"date" (newest first) or "relevance"."""
        return self._cfg["sort"]

    def set_sort(self, sort):
        if sort not in SORTS:
            raise ValueError(sort)
        if sort != self._cfg["sort"]:
            self._cfg["sort"] = sort
            self._changed("settings")
