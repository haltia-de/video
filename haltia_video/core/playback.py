# SPDX-License-Identifier: GPL-2.0
"""Viewing sessions and write ownership, shared by local and web players."""
import copy
import math
import secrets
import time

from .bookmarks import key


class Sessions:
    def __init__(self, library, changed, clock=time.monotonic, limit=8):
        self.library = library
        self.changed = changed
        self.clock = clock
        self.limit = limit
        self.sessions = {}
        self.video_owner = {}
        self.playlist_owner = {}

    def start(self, device, video, playlist=None, queue=None, start_over=False, activate=True):
        if len(self.sessions) >= self.limit:
            raise ValueError("Слишком много активных сеансов просмотра.")
        ident = secrets.token_urlsafe(24)
        video = copy.deepcopy(video)
        video.pop("progress", None)
        video.pop("bookmarked", None)
        playlist = copy.deepcopy(playlist)
        start = None if start_over else self.library.resume_at(video)
        session = {"id": ident, "device": device, "video": video, "playlist": playlist,
                   "queue": tuple(queue or ()), "pos": start or 0,
                   "duration": video.get("duration"), "paused": False, "ended": False,
                   "saved": -30, "generation": 0, "created": self.clock()}
        session["active"] = False
        self.sessions[ident] = session
        if activate:
            self.activate(ident)
        return copy.deepcopy({k: v for k, v in session.items() if k != "queue"})

    def activate(self, ident):
        session = self.get(ident)
        if session["active"]:
            return
        session["active"] = True
        self.video_owner[key("video", session["video"])] = ident
        if session["playlist"]:
            self.playlist_owner[key("playlist", session["playlist"])] = ident
        self.library.played(session["video"])
        self.changed("playback", {"id": ident, "device": session["device"]})

    def get(self, ident, device=None):
        session = self.sessions.get(ident)
        if session is None or (device is not None and session["device"] != device):
            raise ValueError("Сеанс просмотра закрыт.")
        return session

    def report(self, ident, pos, duration=None, paused=False, ended=False, force=False):
        session = self.get(ident)
        if not isinstance(pos, (int, float)) or not math.isfinite(pos) or not 0 <= pos <= 1e9:
            raise ValueError("Некорректная позиция просмотра.")
        if duration is not None and (not isinstance(duration, (int, float)) or
                                     not math.isfinite(duration) or not 0 < duration <= 1e9):
            raise ValueError("Некорректная длительность.")
        session.update(pos=pos, duration=duration, paused=bool(paused), ended=bool(ended))
        session["reported"] = True
        owner = self.video_owner.get(key("video", session["video"])) == ident
        now = self.clock()
        if owner and (force or paused or ended or now - session["saved"] >= 5):
            session["saved"] = now
            self.library.note_position(session["video"], pos, duration, ended=bool(ended))
        playlist = session["playlist"]
        if playlist and self.playlist_owner.get(key("playlist", playlist)) == ident:
            self.library.note_playlist(playlist, session["video"])
        return {"saved": owner}

    def stop(self, ident):
        session = self.sessions.get(ident)
        if session is None:
            return
        if session.get("reported"):
            self.report(ident, session["pos"], session["duration"], session["paused"],
                        session["ended"], force=True)
        del self.sessions[ident]
        vid = key("video", session["video"])
        if self.video_owner.get(vid) == ident:
            self.video_owner.pop(vid, None)
        playlist = session["playlist"]
        if playlist and self.playlist_owner.get(key("playlist", playlist)) == ident:
            self.playlist_owner.pop(key("playlist", playlist), None)
        self.changed("playback_stopped", {"id": ident, "device": session["device"]})

    def stop_source(self, source):
        for ident, session in list(self.sessions.items()):
            if session["video"]["source"] == source:
                self.stop(ident)

    def stop_device(self, device):
        for ident, session in list(self.sessions.items()):
            if session["device"] == device:
                self.stop(ident)
