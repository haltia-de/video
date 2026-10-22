# SPDX-License-Identifier: GPL-2.0
"""The Library owns followed channels, bookmarks, history and settings in
video.json and history.json. playback.json keeps video positions and
playlist cursors independently of history's display limit.

Callers learn about changes through watch(fn): fn(what) with what one of
"follow" (channels added), "unfollow", "details" (what is known about
followed channels changed), "history", "position" (how far a video in
the history was watched), "bookmarks", "playlist_progress",
"playlist_order" and "settings". Not thread-safe:
every call comes from the GTK thread (workers hand results back there).
"""
import os
import time

from bookmarks import Bookmarks, key as bookmark_key

from sources import CONFIG_DIR, DATA_DIR, load_json, save_json

HISTORY_MAX = 100
# A followed channel's avatar, subscribers and description are asked for
# again after this long; a channel followed from a card has none at all.
DETAILS_MAX_AGE = 7 * 86400
# Where a video goes on from: not the first seconds (start over), and not
# its end (watched: start over next time, shown as seen whole).
RESUME_MIN = 10
DONE_TAIL = 10
SCOPES_ALL = "all"
SORTS = ("date", "relevance")


def _same(a, source, cid):
    return a["source"] == source and a["id"] == cid


def _done(pos, duration):
    return bool(duration) and pos > 0 and duration - pos <= DONE_TAIL


class Library:
    def __init__(self, config_dir=None, data_dir=None):
        config_dir = config_dir or CONFIG_DIR
        data_dir = data_dir or DATA_DIR
        self.config_path = os.path.join(config_dir, "video.json")
        self.history_path = os.path.join(data_dir, "history.json")
        self.playback_path = os.path.join(data_dir, "playback.json")
        cfg = load_json(self.config_path) or {}
        cfg.setdefault("channels", [])
        cfg.setdefault("disabled", [])
        cfg.setdefault("scope", SCOPES_ALL)
        cfg.setdefault("sort", SORTS[0])  # search results: newest first
        cfg.pop("source", None)  # the single search source of 1.2
        self._cfg = cfg
        self._history = load_json(self.history_path) or []
        self._playback = load_json(self.playback_path) or {}
        self._playback.setdefault("videos", {})
        self._playback.setdefault("playlists", {})
        self._playback.setdefault("orders", {})
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
            save_json(self.history_path, self._history)
            save_json(self.playback_path, self._playback)
        elif what in ("playlist_progress", "playlist_order"):
            save_json(self.playback_path, self._playback)
        else:
            save_json(self.config_path, self._cfg)
        for fn in list(self._watchers):
            fn(what)

    # --- followed channels ------------------------------------------------
    def channels(self):
        return list(self._cfg["channels"])

    def is_followed(self, source, cid):
        return any(_same(c, source, cid) for c in self._cfg["channels"])

    def follow(self, chan):
        """Follow a channel (again: its entry is replaced). True if new."""
        return self.follow_all([chan]) == 1

    def follow_all(self, chans, now=None):
        """Follow several channels at once (an account's); the number that
        were not followed before."""
        now = now or time.time()
        new = 0
        kept = self._cfg["channels"]
        for chan in chans:
            if not self.is_followed(chan["source"], chan["id"]):
                new += 1
            kept = [c for c in kept if not _same(c, chan["source"], chan["id"])]
            chan = dict(chan)
            if chan.get("thumb") or chan.get("subscribers") is not None:
                chan["updated"] = now  # found with its details
            kept.append(chan)
        self._cfg["channels"] = kept
        if chans:
            self._changed("follow")
        return new

    def unfollow(self, source, cid):
        before = len(self._cfg["channels"])
        self._cfg["channels"] = [c for c in self._cfg["channels"] if not _same(c, source, cid)]
        if len(self._cfg["channels"]) != before:
            self._changed("unfollow")

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

    def played(self, video):
        """Put a video first in the history, once (keeping how far it was
        watched)."""
        old = self.position(video)
        entry = dict(video)
        for key in ("pos", "duration", "ended"):
            if old and key in old:
                entry[key] = old[key]
        self._playback["videos"][bookmark_key("video", video)] = dict(entry)
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

    def playlist_order(self, playlist):
        return self._playback["orders"].get(bookmark_key("playlist", playlist), "vk")

    def set_playlist_order(self, playlist, order):
        if order not in ("vk", "reverse", "lesson"):
            raise ValueError(order)
        if order != self.playlist_order(playlist):
            self._playback["orders"][bookmark_key("playlist", playlist)] = order
            self._changed("playlist_order")

    def note_playlist(self, playlist, video):
        ident = bookmark_key("playlist", playlist)
        old = self._playback["playlists"].get(ident)
        if not old or not _same(old["video"], video["source"], video["id"]):
            self._playback["playlists"][ident] = {"playlist": dict(playlist), "video": dict(video)}
            self._changed("playlist_progress")

    def playlist_resume(self, playlist):
        cursor = self._playback["playlists"].get(bookmark_key("playlist", playlist))
        if not cursor:
            return None
        video = cursor["video"]
        pos = self.position(video) or {}
        return {"video": video, "pos": pos.get("pos"), "duration": pos.get("duration"),
                "complete": self.completed(video)}

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
