# SPDX-License-Identifier: GPL-2.0
"""One serialized sync per captured account. Network work receives only
copies; all Library changes and notifications return through run's done.
"""
import threading

from bookmarks import account_key, key


class Sync:
    def __init__(self, bookmarks, sources, run, changed):
        self.bookmarks = bookmarks
        self.sources = sources
        self.run = run
        self.changed = changed
        self.sessions = {}

    def refresh_accounts(self):
        for source in self.sources.values():
            if not hasattr(source, "bookmark_client"):
                continue
            client = source.bookmark_client()
            current = self.sessions.get(source.id)
            aid = account_key(source.id, {"id": client.id}) if client else None
            if (current and current["aid"] == aid and
                    getattr(current["client"], "active", lambda: True)()):
                continue
            if current:
                current["cancel"].set()
                del self.sessions[source.id]
            if client:
                session = {"aid": aid, "client": client, "kinds": None,
                           "note": "Проверяю синхронизацию закладок…", "busy": False,
                           "cancel": threading.Event(), "again": False}
                self.sessions[source.id] = session
                self.probe(source.id)

    def current(self, sid, session):
        return self.sessions.get(sid) is session and not session["cancel"].is_set()

    def probe(self, sid):
        session = self.sessions.get(sid)
        if not session or session["busy"]:
            return
        session["busy"] = True

        def done(kinds, error):
            if not self.current(sid, session):
                return
            session["busy"] = False
            session["kinds"] = kinds if not error else None
            session["note"] = ("Не удалось проверить синхронизацию. Повторите обновление." if error else
                               ("Синхронизация доступна: %s." % ", ".join(
                                   {"video": "видео", "channel": "каналы", "playlist": "плейлисты"}.get(k, k) for k in kinds)
                                if kinds else "VK не разрешает доступ к закладкам при этом способе входа. "
                                "Закладки сохраняются только на устройстве."))
            self.changed()
            if kinds and not error:
                self.refresh(sid)
        self.run(session["client"].capabilities, done=done)

    def link_for(self, kind, obj):
        s = self.sessions.get(obj["source"])
        if s and kind in (s["kinds"] or []) and self.bookmarks.enabled(s["aid"]):
            return s["aid"], s["client"].name
        return None

    def enable(self, sid, on):
        s = self.sessions[sid]
        if on and not s["kinds"]:
            return
        self.bookmarks.enable(s["aid"], on)
        if not on:
            # Disable invalidates results immediately, even if a network
            # call has already started. A later enable reconciles afresh.
            s["cancel"].set()
            del self.sessions[sid]
            self.refresh_accounts()
        else:
            self.refresh(sid)

    def refresh(self, sid=None):
        for source_id, session in list(self.sessions.items()):
            if sid is not None and sid != source_id:
                continue
            if session["busy"]:
                session["again"] = True
                continue
            if session["kinds"] is None:
                self.probe(source_id)
                continue
            if not session["kinds"] or not self.bookmarks.enabled(session["aid"]):
                continue
            self._sync(source_id, session)

    def _sync(self, sid, session):
        session["busy"] = True
        session["again"] = False
        before = self.bookmarks.snapshot(session["aid"])
        client, kinds, cancel = session["client"], tuple(session["kinds"]), session["cancel"]

        def worker():
            results = {}
            for ident, b in before.items():
                link = b["links"][session["aid"]]
                if cancel.is_set():
                    return None
                if not link.get("action") or b["kind"] not in kinds:
                    continue
                try:
                    client.write(b["kind"], b["object"], link["action"] == "add")
                    results[ident] = (True, None)
                except Exception:
                    results[ident] = (False, "Не удалось отправить изменение. Нажмите «Обновить».")
            if cancel.is_set():
                return None
            try:
                remote = list(client.read(kinds))
                if hasattr(client, "available"):
                    found = {key(kind, obj) for kind, obj in remote}
                    for ident, b in before.items():
                        link = b["links"][session["aid"]]
                        if (ident not in found and b["kind"] in kinds and
                                link["state"] == "synced" and not link.get("action")):
                            if cancel.is_set():
                                return None
                            if not client.available(b["kind"], b["object"]):
                                # Loss of access (including deletion of
                                # the object itself) is not an unfave.
                                obj = dict(b["object"], unavailable="Объект недоступен этому аккаунту.")
                                remote.append((b["kind"], obj))
            except Exception:
                remote = None
            return results, remote

        def done(answer, error):
            if not self.current(sid, session):
                return
            session["busy"] = False
            if answer is not None and not error:
                results, remote = answer
                self.bookmarks.reconcile(session["aid"], client.name, before, results, remote, kinds)
                session["note"] = ("Часть изменений не отправлена. Нажмите «Обновить», чтобы повторить."
                                   if any(not ok for ok, _error in results.values()) else
                                   "Закладки обновлены." if remote is not None else
                                   "Не удалось получить закладки сервиса; сохранённый список оставлен.")
            else:
                session["note"] = "Не удалось обновить закладки. Сохранённый список оставлен."
            self.changed()
            if session["again"]:
                self.refresh(sid)
        self.run(worker, done=done)

    def close(self):
        for s in self.sessions.values():
            s["cancel"].set()
        self.sessions.clear()
