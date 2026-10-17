# SPDX-License-Identifier: GPL-2.0
"""Kept collections: something fetched from the network (the feed, a
source's Главная, a search, a channel's videos), shown at once from what
was kept and fetched again behind it when it is old.

    feed = Kept(fetch, ttl=1800, run=app.work, path=".../feed.json")
    videos = feed.get("feed")     # what is kept now, or None; fetches if old
    feed.watch(fn)                # fn(key, error) when a fetch ended

fetch(key, *prepare(key)) runs through run(func, *args, done=...) (a
worker; done comes back on the caller's thread). prepare, when given, is
called on the caller's thread for what the fetch needs (the followed
channels, a channel): the worker then touches nothing of the caller's.
One fetch per key at a time; a failed one keeps what was kept and is not
tried again for RETRY_SEC. With a path, the values (keys must be text)
are written there after every fetch and read back at the next start.
Not thread-safe: every call comes from the caller's (GTK) thread.
"""
import time

from sources import load_json, save_json

RETRY_SEC = 120
FORMAT = 1


class Kept:
    def __init__(self, fetch, ttl, run, path=None, prepare=None, clock=time.time,
                 retry=RETRY_SEC):
        self.fetch = fetch
        self.ttl = ttl
        self.run = run
        self.path = path
        self.prepare = prepare
        self.clock = clock
        self.retry = retry
        self.entries = {}  # key: {"value": ..., "time": when fetched}
        self.loading = set()
        self.stale = set()  # invalidated while being fetched: old on arrival
        self.failed = {}  # key: when the last fetch failed
        self.watchers = []
        if path:
            kept = load_json(path) or {}
            if kept.get("format") == FORMAT:
                self.entries = kept.get("entries") or {}

    def watch(self, fn):
        self.watchers.append(fn)

    def get(self, key):
        """What is kept for key (None if nothing yet); fetches it again
        when it is older than ttl."""
        entry = self.entries.get(key)
        now = self.clock()
        if (key not in self.loading
                and (entry is None or now - entry["time"] >= self.ttl)
                and now - self.failed.get(key, -self.retry) >= self.retry):
            self._start(key)
        return entry["value"] if entry else None

    def is_loading(self, key):
        return key in self.loading

    def has_failed(self, key):
        """The last fetch of key failed (what is kept, if any, is older)."""
        return key in self.failed

    def invalidate(self, key=None):
        """Old now: the next get fetches again (and may retry at once). A
        fetch already under way may have started before what made it old,
        so what it brings counts as old too."""
        for k in ([key] if key is not None else list(self.entries)):
            if k in self.entries:
                self.entries[k]["time"] = 0
        for k in ([key] if key is not None else list(self.loading)):
            if k in self.loading:
                self.stale.add(k)
        for k in ([key] if key is not None else list(self.failed)):
            self.failed.pop(k, None)

    def forget(self, key=None):
        """Drop what is kept for key, or all (not the account's any more)."""
        for k in ([key] if key is not None else list(self.entries) + list(self.failed)):
            self.entries.pop(k, None)
            self.failed.pop(k, None)

    def _start(self, key):
        self.loading.add(key)
        args = self.prepare(key) if self.prepare else ()
        self.run(self.fetch, key, *args,
                 done=lambda value, error: self._done(key, value, error))

    def _done(self, key, value, error):
        self.loading.discard(key)
        stale = key in self.stale
        self.stale.discard(key)
        if error is not None:
            self.failed[key] = self.clock()
        else:
            self.failed.pop(key, None)
            when = 0 if stale else self.clock()
            self.entries[key] = {"value": value, "time": when}
            if self.path:
                try:
                    save_json(self.path, {"format": FORMAT, "entries": self.entries})
                except OSError:
                    pass
        for fn in list(self.watchers):
            fn(key, error)
