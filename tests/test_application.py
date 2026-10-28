# SPDX-License-Identifier: GPL-2.0
import copy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from library import Library
from haltia_video.core.application import Application

VIDEO = {"source": "vk", "id": "-7_1", "title": "Video", "duration": 600}
PLAYLIST = {"source": "vk", "owner_id": "-7", "id": "7", "title": "Course"}


class ApplicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = Library(self.tmp.name, self.tmp.name)
        self.jobs = []
        self.runtime = SimpleNamespace(work=lambda fn, *args, done: self.jobs.append((fn, args, done)),
                                       later=lambda *args: None)
        self.source = SimpleNamespace(id="vk", label="VK", search=lambda q: [VIDEO],
                                      search_channels=lambda q: [],
                                      playlist_videos=lambda p, offset=0: {"videos": [VIDEO], "next": None})
        self.core = Application({"vk": self.source}, self.library, self.runtime)

    def pump(self):
        while self.jobs:
            fn, args, done = self.jobs.pop(0)
            try:
                value, error = fn(*args), None
            except Exception as exc:
                value, error = None, exc
            done(value, error)

    def test_signing_in_brings_the_accounts_channels_once_a_day(self):
        chans = [{"source": "vk", "id": "-1", "title": "One"}]
        asked = []
        self.source.subscriptions = lambda: asked.append(1) or chans
        self.core._signin_changed(self.source, {"kind": "in", "name": "Me"})
        self.pump()
        self.assertTrue(self.library.is_followed("vk", "-1"))
        self.assertIn("добавлено каналов — 1", self.core.account_notes["vk"])
        self.core._signin_changed(self.source, {"kind": "out"})
        self.core._signin_changed(self.source, {"kind": "in", "name": "Me"})
        self.pump()
        self.assertEqual(len(asked), 1)  # synced less than a day ago

    def test_home_follows_the_persons_sections_with_where_all_goes(self):
        self.library.played(VIDEO)
        self.library.note_position(VIDEO, 120, 600)
        self.library.note_playlist(PLAYLIST, VIDEO)
        home = self.core.query("home")["sections"]
        self.assertEqual([s["id"] for s in home], ["continue", "playlists", "feed"])
        cont, playlists, feed = home
        self.assertEqual(cont["items"][0]["id"], VIDEO["id"])
        self.assertEqual((cont["more"], cont["always"], cont["total"]), ({"view": "history"}, True, 1))
        cursor = playlists["items"][0]
        self.assertEqual((cursor["playlist"]["id"], cursor["video"]["pos"], cursor["chan"]["id"]), ("7", 120, "-7"))
        self.assertIn("подписаться", feed["hint"])  # no channels followed yet
        self.core.command("home_sections", {"sections": [{"id": "bookmarks", "on": True},
                                                         {"id": "continue", "on": False}]})
        self.core.command("bookmark_add", {"kind": "video", "entity": cont["items"][0]})
        home = self.core.query("home")["sections"]
        self.assertEqual([s["id"] for s in home], ["bookmarks", "playlists", "feed"])
        self.assertEqual([e["id"] for e in self.core.query("state")["home_sections"]][:2], ["bookmarks", "continue"])

    def test_what_a_page_showed_comes_back_plain(self):
        shown = self.core._videos([VIDEO])[0]
        self.assertIn("progress", shown)
        self.core.command("bookmark_add", {"kind": "video", "entity": dict(shown, task_done=True)})
        self.core.command("follow", {"entity": {"source": "vk", "id": "-1", "title": "One",
                                                "followed": False, "bookmarked": True}})
        saved = self.library.bookmarks.view()[0]["object"]
        self.assertEqual(saved, VIDEO)
        self.assertNotIn("bookmarked", self.library.channels()[0])

    def test_core_imports_without_gi_or_desktop_kit(self):
        code = '''import sys
class Block:
 def find_spec(self, name, *args):
  if name.split('.')[0] in ('gi', 'haltia_kit', 'aiohttp'):
   raise ImportError(name)
sys.meta_path.insert(0, Block())
from haltia_video.core.application import Application
from haltia_video.core.runtime import Runtime
'''
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)

    def test_queries_return_copies_and_share_a_single_fetch(self):
        p = {"query": "test"}
        self.core.query("search", p, "first")
        self.core.query("search", p, "second")
        self.assertEqual(len(self.jobs), 1)
        self.pump()
        found = self.core.query("search", p)
        found["sections"][0]["items"][0]["title"] = "Mutated"
        self.assertEqual(self.core.query("search", p)["sections"][0]["items"][0]["title"], "Video")

    def test_playlist_sort_is_per_device(self):
        self.core.query("playlist", {"entity": PLAYLIST, "order": "lesson"}, "tablet")
        self.core.query("playlist", {"entity": PLAYLIST, "order": "reverse"}, "phone")
        self.assertEqual(self.library.playlist_order(PLAYLIST, "tablet"), "lesson")
        self.assertEqual(self.library.playlist_order(PLAYLIST, "phone"), "reverse")
        self.assertEqual(self.library.playlist_order(PLAYLIST), "vk")

    def test_new_session_owns_position_even_after_old_pause(self):
        old = self.core.sessions.start("gtk", VIDEO, PLAYLIST)["id"]
        self.core.sessions.report(old, 100, 600, paused=True)
        new = self.core.sessions.start("tablet", VIDEO, PLAYLIST)["id"]
        self.core.sessions.report(new, 200, 600, paused=True)
        self.assertFalse(self.core.sessions.report(old, 400, 600, paused=True)["saved"])
        self.core.sessions.stop(old)
        self.assertEqual(self.library.resume_at(VIDEO), 200)
        self.core.sessions.stop(new)
        self.assertEqual(self.library.resume_at(VIDEO), 200)

    def test_local_queue_releases_previous_sessions_across_many_videos(self):
        player_stops = []
        self.core.watch(lambda e: player_stops.append(e) if e["type"] == "playback_stopped"
                        and e["data"]["id"] == self.core.local_session else None)
        for index in range(12):
            video = dict(VIDEO, id="-7_%d" % index)
            state = {"video": video, "playlist": PLAYLIST, "pos": 100,
                     "duration": 600, "paused": False}
            # These are the actual local player's watcher ordering.
            self.core.note_local_position(state)
            self.core.note_local_played(state)
            self.core.note_local_position(dict(state, paused=True))
            self.assertEqual(len(self.core.sessions.sessions), 1)
            self.assertEqual(self.library.resume_at(video), 100)
        self.core.note_local_position(None)
        self.core.note_local_played(None)
        self.assertEqual(self.core.sessions.sessions, {})
        self.assertEqual(self.library.resume_at(dict(VIDEO, id="-7_0")), 100)
        self.assertEqual(player_stops, [], "natural local transitions must not stop the native player")

    def test_reset_is_confirmed_scoped_and_stops_sessions_before_clearing(self):
        ident = self.core.sessions.start("tablet", VIDEO, PLAYLIST)["id"]
        self.core.sessions.report(ident, 150, 600, paused=True)
        self.core.query("reset_preview", {"entity": PLAYLIST}, "tablet")
        self.pump()
        preview = self.core.query("reset_preview", {"entity": PLAYLIST}, "tablet")
        with self.assertRaises(ValueError):
            self.core.command("reset_progress", preview, "other")
        preview = self.core.query("reset_preview", {"entity": PLAYLIST}, "tablet")
        self.core.command("reset_progress", preview, "tablet")
        self.assertEqual(self.core.sessions.sessions, {})
        self.assertIsNone(self.library.playlist_resume(PLAYLIST))
        self.assertIsNone(self.library.resume_at(VIDEO))

    def test_old_collection_cannot_publish_after_account_epoch_change(self):
        events = []
        self.core.watch(events.append)
        self.core.query("playlist", {"entity": PLAYLIST})
        self.core.reset_channel_data()
        self.pump()
        self.assertFalse(any(e["type"] == "cache" for e in events))
        self.assertEqual(self.core.playlist_pages.entries, {})
