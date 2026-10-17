# SPDX-License-Identifier: GPL-2.0
"""The library: what is the person's own in Видео. The channels they follow
(with what is known about each), the history of what they played, and the
settings (sources switched off, where a search goes, how results are
sorted). Kept in video.json and history.json, written whole on every
change, in the same form as before this module existed.

Callers learn about changes through watch(fn): fn(what) with what one of
"follow" (channels added), "unfollow", "details" (what is known about
followed channels changed), "history" and "settings". Not thread-safe:
every call comes from the GTK thread (workers hand results back there).
"""
import os
import time

from sources import load_json, save_json

HISTORY_MAX = 100
# A followed channel's avatar, subscribers and description are asked for
# again after this long; a channel followed from a card has none at all.
DETAILS_MAX_AGE = 7 * 86400
SCOPES_ALL = "all"
SORTS = ("date", "relevance")


def _same(a, source, cid):
    return a["source"] == source and a["id"] == cid


class Library:
    def __init__(self, config_dir=None, data_dir=None):
        config_dir = config_dir or os.path.expanduser("~/.config/b8k")
        data_dir = data_dir or os.path.expanduser("~/.local/share/b8k-video")
        self.config_path = os.path.join(config_dir, "video.json")
        self.history_path = os.path.join(data_dir, "history.json")
        cfg = load_json(self.config_path) or {}
        cfg.setdefault("channels", [])
        cfg.setdefault("disabled", [])
        cfg.setdefault("scope", SCOPES_ALL)
        cfg.setdefault("sort", SORTS[0])  # search results: newest first
        cfg.pop("source", None)  # the single search source of 1.2
        self._cfg = cfg
        self._history = load_json(self.history_path) or []
        self._watchers = []

    def watch(self, fn):
        self._watchers.append(fn)

    def _changed(self, what):
        if what == "history":
            save_json(self.history_path, self._history)
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
        """Put a video first in the history, once."""
        self._history = [video] + [v for v in self._history
                                   if not (v["id"] == video["id"]
                                           and v["source"] == video["source"])]
        del self._history[HISTORY_MAX:]
        self._changed("history")

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
