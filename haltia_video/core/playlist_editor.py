# SPDX-License-Identifier: GPL-2.0
"""Bounded, device-owned drafts. Saving checks both account and collection.
Only IDs are copied; metadata remains in the bounded source collection.
"""
import secrets
import time

class Editor:
    def __init__(self, library, clock=time.monotonic):
        self.library, self.clock = library, clock
        self.drafts = {}

    def begin(self, device, playlist, mode, full, videos, epoch):
        for token, draft in list(self.drafts.items()):
            if draft["until"] < self.clock():
                del self.drafts[token]
        if len(self.drafts) >= 16:
            raise ValueError("Слишком много открытых редакторов. Закройте лишние.")
        token = secrets.token_urlsafe(24)
        bounds = []
        if mode == "lesson":
            start = 0
            while start < len(videos):
                end = start + 1
                g = full["_groups"][videos[start]["entry_id"]]
                while end < len(videos) and full["_groups"][videos[end]["entry_id"]] == g:
                    end += 1
                bounds.extend([(start, end - 1)] * (end - start))
                start = end
        self.drafts[token] = {"device": device, "playlist": dict(playlist), "order": mode,
                              "collection": full["_collection"], "epoch": epoch, "bounds": bounds,
                              "version": self.library.edit_version(playlist),
                              "ids": [v["entry_id"] for v in videos], "until": self.clock() + 3600}
        return token

    def get(self, token, device, full, epoch):
        d = self.drafts.get(token)
        if not d or d["device"] != device or d["epoch"] != epoch or d["until"] < self.clock():
            raise ValueError("Черновик устарел. Загрузите свежий вариант.")
        if d["collection"] != full["_collection"] or d["version"] != self.library.edit_version(d["playlist"]):
            raise ValueError("Порядок уже изменён на другом устройстве или в сервисе. Загрузите свежий вариант.")
        return d

    def move(self, draft, full, ident, position):
        ids = draft["ids"]
        if ident not in ids or not isinstance(position, int) or not 0 <= position < len(ids):
            raise ValueError("Некорректная позиция.")
        old = ids.index(ident)
        if draft["order"] == "lesson" and full["_groups"][ident] != full["_groups"][ids[position]]:
            raise ValueError("Перестановка разрешена только внутри одного номера урока.")
        ids.insert(position, ids.pop(old))

    def save(self, draft):
        # Keep absent/hidden IDs in their slots so a temporary access failure
        # does not destroy the user's corrections when a video comes back.
        ids = draft["ids"]
        active = set(ids)
        old = self.library.manual_order(draft["playlist"], draft["order"])
        wanted = iter(ids)
        merged = [next(wanted) if ident in active else ident for ident in old]
        merged.extend(wanted)
        self.library.save_manual_order(draft["playlist"], draft["order"], merged, draft["version"])
