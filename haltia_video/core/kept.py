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
import sys


RETRY_SEC = 120
FORMAT = 1


def footprint(value):
    """Approximate owned Python bytes, counting shared objects once.
    Called on the fetch worker, not while drawing a page on slow CPUs.
    """
    pending, seen, size = [value], set(), 0
    while pending:
        item = pending.pop()
        ident = id(item)
        if ident in seen:
            continue
        seen.add(ident)
        size += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return size



class Denied(Exception):
    """A service refused what was asked to this account, or to a guest: a
    private playlist, videos for subscribers only. Sources raise it (the
    sources package names it); a page then says so, not «нет связи?»."""

class Kept:
    def __init__(self, fetch, ttl, run, path=None, prepare=None, clock=time.time,
                 retry=RETRY_SEC, max_entries=64, load=None, save=None,
                 max_bytes=8 * 1024 * 1024):
        self.fetch = fetch
        self.ttl = ttl
        self.run = run
        self.path = path
        self.load = load
        self.save = save
        if path and (load is None or save is None):
            raise ValueError("persistent caches need storage callbacks")
        self.prepare = prepare
        self.clock = clock
        self.retry = retry
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.entries = {}  # key: {"value": ..., "time": when fetched}
        self.loading = set()
        self.stale = set()  # invalidated while being fetched: old on arrival
        self.failed = {}  # key: when the last fetch failed
        self.errors = {}  # key: what the last fetch failed with
        self.watchers = []
        if path:
            kept = self.load(path) or {}
            if kept.get("format") == FORMAT:
                self.entries = kept.get("entries") or {}
                # Old cache files did not store a byte count. Enforce the same
                # limits after restarting rather than trusting persisted sizes.
                for entry in self.entries.values():
                    entry["bytes"] = footprint(entry["value"])
                self._trim()

    def _trim(self, keep=None):
        while (len(self.entries) > self.max_entries or
               sum(e["bytes"] for e in self.entries.values()) > self.max_bytes):
            candidates = [k for k in self.entries if k != keep] or list(self.entries)
            oldest = min(candidates, key=lambda k: self.entries[k]["time"])
            del self.entries[oldest]

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

    def peek(self, key):
        """What is already kept, without starting a fetch (an optional
        full playlist must not load just to draw its resume block)."""
        entry = self.entries.get(key)
        return entry["value"] if entry else None

    def is_loading(self, key):
        return key in self.loading

    def has_failed(self, key):
        """The last fetch of key failed (what is kept, if any, is older)."""
        return key in self.failed

    def denied(self, key):
        """The last fetch of key failed because the service refused it."""
        return isinstance(self.errors.get(key), Denied)

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
            self.errors.pop(k, None)

    def forget(self, key=None):
        """Drop what is kept for key, or all (not the account's any more)."""
        for k in ([key] if key is not None else list(self.entries) + list(self.failed)):
            self.entries.pop(k, None)
            self.failed.pop(k, None)
            self.errors.pop(k, None)

    def _start(self, key):
        self.loading.add(key)
        args = self.prepare(key) if self.prepare else ()
        def fetch():
            value = self.fetch(key, *args)
            return value, footprint(value)

        def done(answer, error):
            value, size = answer if error is None else (None, 0)
            self._done(key, value, error, size)
        self.run(fetch, done=done)

    def _done(self, key, value, error, size=0):
        self.loading.discard(key)
        stale = key in self.stale
        self.stale.discard(key)
        if error is None and size > self.max_bytes:
            error = ValueError("collection exceeds cache budget")
        if error is not None:
            self.failed[key] = self.clock()
            self.errors[key] = error
        else:
            self.failed.pop(key, None)
            self.errors.pop(key, None)
            when = 0 if stale else self.clock()
            self.entries[key] = {"value": value, "time": when, "bytes": size}
            self._trim(keep=key)
            if self.path:
                try:
                    self.save(self.path, {"format": FORMAT, "entries": self.entries})
                except OSError:
                    pass
        while len(self.failed) > self.max_entries:
            self.failed.pop(next(iter(self.failed)))
        for fn in list(self.watchers):
            fn(key, error)
