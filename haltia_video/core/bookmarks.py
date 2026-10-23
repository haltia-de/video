# SPDX-License-Identifier: GPL-2.0
"""Bookmarks and account reconciliation, without GTK or network calls.

The Library owns persistence. Every mutation is on its thread; sync takes
copies to a worker and applies only replies belonging to the same edit.
"""
import copy
import time
import uuid

KINDS = (("video", "Видео"), ("playlist", "Плейлисты"), ("channel", "Каналы"))
SORTS = (("added", "Время добавления"), ("title", "Название"), ("source", "Сервис"))


def key(kind, obj):
    owner = obj.get("owner_id", "") if kind == "playlist" and obj["source"] == "vk" else ""
    return "|".join((obj["source"], kind, str(owner), str(obj["id"])))


def account_key(source, account):
    return "%s:%s" % (source, account["id"])


def text(value):
    return (value or "").casefold().replace("ё", "е")


class Bookmarks:
    def __init__(self, data, changed):
        self.data = data
        data.setdefault("items", [])
        data.setdefault("accounts", {})
        self.changed = changed

    def get(self, ident):
        return next((b for b in self.data["items"] if b["key"] == ident), None)

    def find(self, kind, obj):
        b = self.get(key(kind, obj))
        return b if b and not b.get("deleted") else None

    def add(self, kind, obj, link=None, now=None):
        ident = key(kind, obj)
        old = self.get(ident)
        if old and not old.get("deleted"):
            return old
        if old:
            # A removal in flight must finish before a re-add is sent.
            old.pop("deleted", None)
            old["object"] = copy.deepcopy(obj)
            old["local"] = True
            for aid in list(old["links"]):
                self._edit(old, aid, "add")
            b = old
        else:
            b = {"key": ident, "kind": kind, "object": copy.deepcopy(obj),
                 "added": time.time() if now is None else now, "local": True, "links": {}}
            self.data["items"].append(b)
        if link:
            aid, account = link
            self._edit(b, aid, "add", account)
            self._unignore(aid, ident)
        self.changed()
        return b

    def _edit(self, b, aid, action, account=None):
        old = b["links"].get(aid) or {}
        b["links"][aid] = {"account": account or old.get("account", ""),
                           "state": "pending", "action": action,
                           "revision": uuid.uuid4().hex}

    def link(self, ident, aid, account):
        b = self.get(ident)
        if not b or b.get("deleted"):
            return
        self._edit(b, aid, "add", account)
        self._unignore(aid, ident)
        self.changed()

    def remove(self, ident, local_only=False):
        b = self.get(ident)
        if not b:
            return
        b.pop("pin", None)
        if not b["links"]:
            self.data["items"].remove(b)
        else:
            b["deleted"] = not local_only
            b["local"] = local_only
            for aid in list(b["links"]):
                self._edit(b, aid, "detach" if local_only else "remove")
        self.changed()

    def pin(self, ident, on):
        b = self.get(ident)
        if b:
            if on and "pin" not in b:
                b["pin"] = max((v.get("pin", -1) for v in self.data["items"]), default=-1) + 1
            elif not on:
                b.pop("pin", None)
            self.changed()

    def move(self, ident, step):
        pins = sorted((b for b in self.data["items"] if "pin" in b and not b.get("deleted")),
                      key=lambda b: b["pin"])
        for i, b in enumerate(pins):
            if b["key"] == ident and 0 <= i + step < len(pins):
                pins[i], pins[i + step] = pins[i + step], pins[i]
                for pos, item in enumerate(pins):
                    item["pin"] = pos
                self.changed()
                return

    def view(self, query="", kind="all", source="all", state="all", sort="added", reverse=True):
        words = text(query).split()
        out = []
        for b in self.data["items"]:
            obj = b["object"]
            if b.get("deleted") or (kind != "all" and b["kind"] != kind):
                continue
            if source != "all" and obj["source"] != source:
                continue
            if state != "all" and bool(b["links"]) != (state == "synced"):
                continue
            haystack = text(" ".join(str(obj.get(k) or "") for k in
                                     ("title", "channel", "owner_title", "handle")))
            if all(word in haystack for word in words):
                out.append(b)
        def order(b):
            obj = b["object"]
            if sort == "title":
                return text(obj.get("title"))
            if sort == "source":
                return obj["source"], text(obj.get("title"))
            return b["added"]
        pins = sorted((b for b in out if "pin" in b), key=lambda b: b["pin"])
        return pins + sorted((b for b in out if "pin" not in b), key=order, reverse=reverse)

    def enable(self, aid, on):
        self.data["accounts"].setdefault(aid, {})["enabled"] = bool(on)
        self.changed()

    def enabled(self, aid):
        return self.data["accounts"].get(aid, {}).get("enabled", False)

    def _unignore(self, aid, ident):
        account = self.data["accounts"].setdefault(aid, {})
        account["ignored"] = [k for k in account.get("ignored", []) if k != ident]

    def snapshot(self, aid):
        return {b["key"]: copy.deepcopy(b) for b in self.data["items"] if aid in b["links"]}

    def reconcile(self, aid, name, before, results, remote, kinds):
        """remote is None on a failed/incomplete read: absence then proves
        nothing. Edits made while the worker was away always win."""
        account = self.data["accounts"].setdefault(aid, {})
        ignored = set(account.get("ignored", []))
        for ident, (ok, error) in results.items():
            b = self.get(ident)
            if not b or b["links"].get(aid) != before[ident]["links"][aid]:
                continue
            link = b["links"][aid]
            if not ok:
                link.update(state="error", error=error)
            elif link["action"] in ("remove", "detach"):
                ignored.add(ident)
                del b["links"][aid]
                if b.get("deleted") and not b["links"]:
                    self.data["items"].remove(b)
            else:
                link.update(state="synced", action=None)
                link.pop("error", None)
        account["ignored"] = sorted(ignored)
        if remote is not None:
            found = {key(kind, obj): (kind, obj) for kind, obj in remote}
            for ident, old in before.items():
                b = self.get(ident)
                if not b or b["kind"] not in kinds or ident in found:
                    continue
                link = b["links"].get(aid)
                # Only a previously confirmed remote bookmark can vanish.
                # A failed add and an in-flight local edit stay retryable.
                if (old["links"][aid].get("state") == "synced" and
                        link == old["links"][aid]):
                    del b["links"][aid]
                    if not b["links"]:
                        self.data["items"].remove(b)
            for ident, (kind, obj) in found.items():
                if ident in ignored:
                    continue
                b = self.get(ident)
                if b:
                    link = b["links"].get(aid)
                    if b.get("deleted") or (link and link.get("action")):
                        continue
                    # Do not replace the saved card by an unavailable stub.
                    if not obj.get("unavailable"):
                        b["object"].update(obj)
                        b["object"].pop("unavailable", None)
                    else:
                        b["object"]["unavailable"] = obj["unavailable"]
                else:
                    b = {"key": ident, "kind": kind, "object": copy.deepcopy(obj),
                         "added": obj.get("bookmarked_at", time.time()),
                         "local": False, "links": {}}
                    self.data["items"].append(b)
                b["links"][aid] = {"account": name, "state": "synced", "action": None,
                                   "revision": uuid.uuid4().hex}
        self.changed()
