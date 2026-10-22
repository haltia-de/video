# SPDX-License-Identifier: GPL-2.0
import tempfile
import unittest

from library import HISTORY_MAX, Library
from playlist import collect, continuation

PLAYLIST = {"source": "vk", "owner_id": "-7", "id": "1", "title": "Курс"}
VIDEOS = [{"source": "vk", "id": "-7_%d" % i, "title": "Урок %d" % i} for i in range(65)]


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = Library(self.tmp.name, self.tmp.name)

    def watch(self, video, playlist=PLAYLIST, pos=123, duration=600, ended=False):
        self.lib.played(video)
        self.lib.note_playlist(playlist, video)
        self.lib.note_position(video, pos, duration, ended=ended)

    def test_position_and_playlist_order_survive_restart_and_history_eviction(self):
        self.watch(VIDEOS[2])
        self.lib.set_playlist_order(PLAYLIST, "lesson")
        for i in range(HISTORY_MAX + 1):
            self.lib.played(dict(VIDEOS[0], id="other%d" % i))
        again = Library(self.tmp.name, self.tmp.name)
        self.assertEqual(again.playlist_order(PLAYLIST), "lesson")
        self.assertEqual(again.playlist_resume(PLAYLIST)["pos"], 123)
        self.assertEqual(again.resume_at(VIDEOS[2]), 123)
        self.assertNotIn(VIDEOS[2]["id"], [v["id"] for v in again.history()])

    def test_shared_video_position_but_independent_playlist_cursors(self):
        other = dict(PLAYLIST, id="2")
        self.watch(VIDEOS[1], PLAYLIST, pos=50)
        self.watch(VIDEOS[1], other, pos=150)
        self.assertEqual(self.lib.playlist_resume(PLAYLIST)["pos"], 150)
        self.watch(VIDEOS[2], other, pos=250)
        self.assertEqual(self.lib.playlist_resume(PLAYLIST)["video"]["id"], VIDEOS[1]["id"])
        self.assertEqual(self.lib.playlist_resume(other)["video"]["id"], VIDEOS[2]["id"])

    def test_natural_end_without_known_duration_is_complete(self):
        self.watch(VIDEOS[1], pos=100, duration=None, ended=True)
        saved = self.lib.playlist_resume(PLAYLIST)
        self.assertTrue(saved["complete"])
        self.assertIsNone(self.lib.resume_at(VIDEOS[1]))
        self.lib.note_position(VIDEOS[1], 2, 600)
        self.assertFalse(self.lib.completed(VIDEOS[1]))

    def test_last_ten_seconds_but_not_ninety_five_percent(self):
        self.watch(VIDEOS[1], pos=3500, duration=3600)
        self.assertFalse(self.lib.completed(VIDEOS[1]))
        self.lib.note_position(VIDEOS[1], 3590, 3600)
        self.assertTrue(self.lib.completed(VIDEOS[1]))

    def test_current_order_determines_next_and_end(self):
        self.watch(VIDEOS[1], pos=600)
        saved = self.lib.playlist_resume(PLAYLIST)
        self.assertEqual(continuation(VIDEOS, saved)["video"], VIDEOS[2])
        self.assertEqual(continuation(list(reversed(VIDEOS)), saved)["video"], VIDEOS[0])
        self.assertEqual(continuation([VIDEOS[0], VIDEOS[1]], saved)["kind"], "finished")

    def test_missing_last_video_offers_first_available_and_empty_preserves_cursor(self):
        self.watch(VIDEOS[1])
        saved = self.lib.playlist_resume(PLAYLIST)
        result = continuation([VIDEOS[3], VIDEOS[2]], saved)
        self.assertEqual((result["kind"], result["video"]), ("missing", VIDEOS[3]))
        self.assertEqual(continuation([], saved)["kind"], "empty")
        self.assertEqual(self.lib.playlist_resume(PLAYLIST), saved)

    def test_raw_source_pages_are_preserved_after_filtering(self):
        calls = []
        def fetch(playlist, offset):
            calls.append(offset)
            return {"videos": VIDEOS[0:1] if offset == 0 else VIDEOS[40:42],
                    "next": 30 if offset == 0 else None}
        orders = collect(fetch, PLAYLIST, locations=True)
        self.assertEqual(orders["_locations"][("vk", VIDEOS[41]["id"])], (30, (0,)))
        self.assertEqual(orders["_pages"][30]["videos"], VIDEOS[40:42])
        self.assertEqual(calls, [0, 30])

    def test_reset_clears_shared_positions_and_cursor_but_keeps_sort_bookmarks_and_history(self):
        other = dict(PLAYLIST, id="2")
        self.watch(VIDEOS[1], PLAYLIST, pos=600, ended=True)
        self.watch(VIDEOS[1], other, pos=600, ended=True)
        self.watch(VIDEOS[2], other, pos=200)
        self.lib.set_playlist_order(PLAYLIST, "lesson")
        self.lib.bookmarks.add("playlist", PLAYLIST)
        history = [v["id"] for v in self.lib.history()]
        self.lib.reset_playlist_progress(PLAYLIST, [VIDEOS[1]])
        again = Library(self.tmp.name, self.tmp.name)
        self.assertIsNone(again.playlist_resume(PLAYLIST))
        self.assertIsNone(again.resume_at(VIDEOS[1]))
        self.assertFalse(again.completed(VIDEOS[1]))
        self.assertIsNone(again.progress(VIDEOS[1]))
        self.assertEqual(again.resume_at(VIDEOS[2]), 200)
        self.assertEqual(again.playlist_resume(other)["video"]["id"], VIDEOS[2]["id"])
        self.assertEqual(again.playlist_order(PLAYLIST), "lesson")
        self.assertIsNotNone(again.bookmarks.find("playlist", PLAYLIST))
        self.assertEqual([v["id"] for v in again.history()], history)

    def test_reset_includes_old_cursor_video_removed_from_playlist(self):
        self.watch(VIDEOS[1])
        self.lib.reset_playlist_progress(PLAYLIST, [])
        self.assertIsNone(self.lib.playlist_resume(PLAYLIST))
        self.assertIsNone(self.lib.resume_at(VIDEOS[1]))

    def test_reset_without_cursor_clears_progress_watched_elsewhere(self):
        self.lib.played(VIDEOS[1])
        self.lib.note_position(VIDEOS[1], 200, 600)
        self.lib.reset_playlist_progress(PLAYLIST, [VIDEOS[1]])
        self.assertIsNone(self.lib.resume_at(VIDEOS[1]))
