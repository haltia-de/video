# SPDX-License-Identifier: GPL-2.0
import tempfile
import unittest
from types import SimpleNamespace

from bookmark_sync import Sync
from bookmarks import Bookmarks, key
from library import Library

VIDEO = {"source": "vk", "id": "-7_1", "title": "Урок Ёж", "channel": "Школа"}
CHANNEL = {"source": "vk", "id": "-7", "title": "Школа"}
PLAYLIST = {"source": "vk", "id": "7", "owner_id": "-7", "title": "Курс"}
AID = "vk:1"


class BookmarksTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.b = Bookmarks({}, lambda: self.events.append(True))

    def add_linked(self):
        return self.b.add("video", VIDEO, (AID, "Account"))

    def confirm(self, remote=None, kinds=("video", "channel")):
        before = self.b.snapshot(AID)
        results = {ident: (True, None) for ident, b in before.items()
                   if b["links"][AID].get("action")}
        self.b.reconcile(AID, "Account", before, results,
                         remote if remote is not None else [("video", VIDEO)], kinds)

    def test_persistence_without_subscribing_or_changing_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp, tmp)
            saved = lib.bookmarks.add("playlist", PLAYLIST)
            lib.bookmarks.pin(saved["key"], True)
            again = Library(tmp, tmp)
            self.assertEqual(again.bookmarks.view(), lib.bookmarks.view())
            self.assertEqual(again.channels(), [])
            self.assertEqual(again.history(), [])

    def test_identity_includes_kind_source_and_vk_playlist_owner(self):
        self.b.add("playlist", PLAYLIST)
        self.b.add("playlist", dict(PLAYLIST, owner_id="-8"))
        self.b.add("channel", CHANNEL)
        self.b.add("video", VIDEO)
        self.b.add("video", dict(VIDEO, source="youtube"))
        self.b.add("video", VIDEO)
        self.assertEqual(len(self.b.view()), 5)
        self.assertEqual(key("playlist", dict(PLAYLIST, source="youtube")),
                         key("playlist", dict(PLAYLIST, source="youtube", owner_id="")))

    def test_combined_filters_and_normalized_search(self):
        self.add_linked()
        self.b.add("channel", CHANNEL)
        self.b.add("video", dict(VIDEO, source="youtube"))
        found = self.b.view("еж школа", kind="video", source="vk", state="synced")
        self.assertEqual(len(found), 1)
        self.assertEqual(len(self.b.view(state="local")), 2)

    def test_pins_ignore_sort_and_filters_do_not_lose_global_pin_order(self):
        a = self.b.add("video", VIDEO, now=1)
        b = self.b.add("channel", CHANNEL, now=2)
        c = self.b.add("playlist", PLAYLIST, now=3)
        self.b.pin(a["key"], True)
        self.b.pin(c["key"], True)
        self.b.move(c["key"], -1)
        for sort in ("title", "added", "source"):
            self.assertEqual([x["key"] for x in self.b.view(sort=sort)],
                             [c["key"], a["key"], b["key"]])

    def test_transient_read_failure_keeps_all_bookmarks(self):
        b = self.add_linked()
        self.confirm()
        self.b.reconcile(AID, "Account", self.b.snapshot(AID), {}, None, ("video",))
        self.assertIsNotNone(self.b.get(b["key"]))

    def test_remote_deletion_only_applies_to_confirmed_supported_kind(self):
        b = self.add_linked()
        self.confirm()
        before = self.b.snapshot(AID)
        self.b.reconcile(AID, "Account", before, {}, [], ("channel",))
        self.assertIsNotNone(self.b.get(b["key"]))
        self.b.reconcile(AID, "Account", before, {}, [], ("video",))
        self.assertIsNone(self.b.get(b["key"]))

    def test_failed_add_survives_absence_and_can_retry(self):
        b = self.add_linked()
        self.b.reconcile(AID, "Account", self.b.snapshot(AID),
                         {b["key"]: (False, "offline")}, [], ("video",))
        self.assertEqual(b["links"][AID]["state"], "error")
        self.assertEqual(len(self.b.view(state="synced")), 1)
        self.confirm()
        self.assertEqual(b["links"][AID]["state"], "synced")

    def test_delete_tombstone_persists_until_remote_acknowledges(self):
        b = self.add_linked()
        self.confirm()
        self.b.remove(b["key"])
        self.assertEqual(self.b.view(), [])
        self.assertIsNotNone(self.b.get(b["key"]))
        self.confirm([])
        self.assertIsNone(self.b.get(b["key"]))

    def test_keep_local_does_not_import_it_again(self):
        b = self.add_linked()
        self.confirm()
        self.b.remove(b["key"], local_only=True)
        self.confirm()  # stale remote list still contains the removed object
        self.assertEqual(b["links"], {})
        self.confirm()
        self.assertEqual(len(self.b.view(state="local")), 1)
        self.b.link(b["key"], AID, "Account")
        self.confirm()
        self.assertTrue(b["links"])

    def test_late_write_does_not_overwrite_new_local_intent(self):
        b = self.add_linked()
        before = self.b.snapshot(AID)
        self.b.remove(b["key"], local_only=True)
        self.b.reconcile(AID, "Account", before, {b["key"]: (True, None)},
                         [("video", VIDEO)], ("video",))
        self.assertEqual(b["links"][AID]["action"], "detach")

    def test_account_links_are_independent(self):
        b = self.add_linked()
        self.confirm()
        self.b.link(b["key"], "vk:2", "Other")
        self.b.reconcile(AID, "Account", self.b.snapshot(AID), {}, [], ("video",))
        self.assertNotIn(AID, b["links"])
        self.assertIn("vk:2", b["links"])


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.b = Bookmarks({}, lambda: None)
        self.jobs = []
        self.writes = []
        self.remote = []
        self.client = SimpleNamespace(id="1", name="Account", capabilities=lambda: ["video"],
                                      read=lambda kinds: self.remote,
                                      write=lambda *a: self.writes.append(a))
        self.src = SimpleNamespace(id="vk", bookmark_client=lambda: self.client)
        self.sync = Sync(self.b, {"vk": self.src},
                         lambda fn, done: self.jobs.append((fn, done)), lambda: None)

    def pump(self):
        while self.jobs:
            fn, done = self.jobs.pop(0)
            try:
                result, error = fn(), None
            except Exception as exc:
                result, error = None, exc
            done(result, error)

    def test_probe_does_not_write_or_import_until_enabled(self):
        self.remote = [("video", VIDEO)]
        self.sync.refresh_accounts()
        self.pump()
        self.assertEqual(self.b.view(), [])
        self.assertEqual(self.writes, [])
        self.sync.enable("vk", True)
        self.pump()
        self.assertEqual(len(self.b.view()), 1)

    def test_account_replacement_cancels_old_jobs(self):
        self.sync.refresh_accounts()
        self.pump()
        self.b.enable(AID, True)
        self.b.add("video", VIDEO, (AID, "Account"))
        self.sync.refresh()
        self.client = SimpleNamespace(id="2", name="Other", capabilities=lambda: [])
        self.sync.refresh_accounts()
        self.pump()
        self.assertEqual(self.writes, [])
        self.assertEqual(self.b.view()[0]["links"][AID]["state"], "pending")

    def test_probe_failure_does_not_retry_forever(self):
        def fail():
            raise OSError("offline")
        self.client.capabilities = fail
        self.sync.refresh_accounts()
        fn, done = self.jobs.pop(0)
        done(None, OSError("offline"))
        self.assertEqual(self.jobs, [])

    def test_new_local_bookmarks_not_uploaded_on_enable(self):
        self.b.add("video", VIDEO)
        self.sync.refresh_accounts()
        self.pump()
        self.sync.enable("vk", True)
        self.pump()
        self.assertEqual(self.writes, [])

    def test_loss_of_access_is_not_remote_deletion(self):
        self.remote = [("video", VIDEO)]
        self.client.available = lambda kind, obj: False
        self.sync.refresh_accounts()
        self.pump()
        self.sync.enable("vk", True)
        self.pump()
        self.remote = []
        self.sync.refresh()
        self.pump()
        self.assertEqual(len(self.b.view()), 1)
        self.assertTrue(self.b.view()[0]["object"].get("unavailable"))
        self.client.available = lambda kind, obj: True
        self.sync.refresh()
        self.pump()
        self.assertEqual(self.b.view(), [])
