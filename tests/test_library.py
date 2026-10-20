# SPDX-License-Identifier: GPL-2.0
import json
import os
import tempfile
import unittest

from library import DETAILS_MAX_AGE, HISTORY_MAX, Library

YT = {"source": "youtube", "id": "UC1", "title": "One"}
VK = {"source": "vk", "id": "-2", "title": "Two", "thumb": "https://a/b.jpg", "subscribers": 5}


def video(n, source="youtube"):
    return {"source": source, "id": "v%d" % n, "title": "V%d" % n}


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, "conf")
        self.data = os.path.join(self.tmp.name, "data")
        self.events = []

    def library(self):
        lib = Library(self.conf, self.data)
        lib.watch(self.events.append)
        return lib

    def stored(self, name="video.json", where=None):
        with open(os.path.join(where or self.conf, name), encoding="utf-8") as fh:
            return json.load(fh)

    def test_empty_start(self):
        lib = self.library()
        self.assertEqual(lib.channels(), [])
        self.assertEqual(lib.history(), [])
        self.assertEqual((lib.scope, lib.sort), ("all", "date"))

    def test_reads_and_migrates_a_1_2_config(self):
        os.makedirs(self.conf)
        with open(os.path.join(self.conf, "video.json"), "w") as fh:
            json.dump({"channels": [YT], "source": "vk"}, fh)
        lib = self.library()
        self.assertEqual(lib.channels(), [YT])
        lib.set_sort("relevance")
        self.assertNotIn("source", self.stored())

    def test_follow_saves_and_tells(self):
        lib = self.library()
        self.assertTrue(lib.follow(YT))
        self.assertTrue(lib.is_followed("youtube", "UC1"))
        self.assertEqual(self.stored()["channels"], [YT])
        self.assertEqual(self.events, ["follow"])
        # It survives a new start.
        self.assertTrue(Library(self.conf, self.data).is_followed("youtube", "UC1"))

    def test_follow_again_replaces_not_duplicates(self):
        lib = self.library()
        lib.follow(YT)
        self.assertFalse(lib.follow(dict(YT, title="Renamed")))
        self.assertEqual([c["title"] for c in lib.channels()], ["Renamed"])

    def test_found_with_details_counts_as_fresh(self):
        lib = self.library()
        lib.follow_all([YT, VK], now=1000)
        self.assertEqual([c["id"] for c in lib.lacking_details(now=1001)], ["UC1"])
        self.assertEqual(len(lib.lacking_details(now=1000 + DETAILS_MAX_AGE + 1)), 2)

    def test_follow_all_counts_new_and_tells_once(self):
        lib = self.library()
        lib.follow(YT)
        self.events.clear()
        self.assertEqual(lib.follow_all([YT, VK]), 1)
        self.assertEqual(self.events, ["follow"])
        self.assertEqual(len(lib.channels()), 2)

    def test_same_id_in_two_sources_is_two_channels(self):
        lib = self.library()
        lib.follow(YT)
        lib.follow(dict(YT, source="vk"))
        self.assertEqual(len(lib.channels()), 2)
        lib.unfollow("vk", "UC1")
        self.assertEqual(lib.channels(), [YT])

    def test_unfollow_unknown_says_nothing(self):
        lib = self.library()
        lib.unfollow("youtube", "nope")
        self.assertEqual(self.events, [])

    def test_details_and_latest(self):
        lib = self.library()
        lib.follow(YT)
        self.events.clear()
        lib.add_details([dict(YT, subscribers=7, title="One!")], now=500)
        chan = lib.channels()[0]
        self.assertEqual((chan["subscribers"], chan["title"], chan["updated"]), (7, "One!", 500))
        lib.note_latest({("youtube", "UC1"): 123.0})
        lib.note_latest({("youtube", "UC1"): 123.0})  # nothing new: no event
        self.assertEqual(lib.channels()[0]["latest"], 123.0)
        self.assertEqual(self.events, ["details", "details"])

    def test_details_of_unfollowed_channel_are_dropped(self):
        lib = self.library()
        lib.add_details([VK])
        self.assertEqual((lib.channels(), self.events), ([], []))

    def test_history_newest_first_once_and_capped(self):
        lib = self.library()
        for n in range(HISTORY_MAX + 5):
            lib.played(video(n))
        lib.played(video(50))
        hist = lib.history()
        self.assertEqual(len(hist), HISTORY_MAX)
        self.assertEqual(hist[0]["id"], "v50")
        self.assertEqual([v["id"] for v in hist].count("v50"), 1)
        self.assertEqual(self.stored("history.json", self.data)[0]["id"], "v50")
        self.assertEqual(self.events[-1], "history")

    def test_history_tells_sources_apart(self):
        lib = self.library()
        lib.played(video(1))
        lib.played(video(1, "vk"))
        self.assertEqual(len(lib.history()), 2)

    def test_position_is_kept_and_resumed(self):
        lib = self.library()
        lib.played(video(1))
        self.events.clear()
        lib.note_position(video(1), 754.0, 2700.0)
        self.assertEqual(self.events, ["position"])
        self.assertEqual(lib.resume_at(video(1)), 754.0)
        self.assertAlmostEqual(lib.progress(video(1)), 754 / 2700)
        again = Library(self.conf, self.data)  # written to history.json
        self.assertEqual(again.resume_at(video(1)), 754.0)

    def test_the_first_seconds_start_over(self):
        lib = self.library()
        lib.played(video(1))
        lib.note_position(video(1), 9.0, 600.0)
        self.assertIsNone(lib.resume_at(video(1)))

    def test_watched_to_the_end_starts_over_and_shows_whole(self):
        lib = self.library()
        lib.played(video(1))
        lib.note_position(video(1), 580.0, 600.0)  # 20 s left
        self.assertIsNone(lib.resume_at(video(1)))
        self.assertEqual(lib.progress(video(1)), 1.0)
        lib.played(video(2))
        lib.note_position(video(2), 3500.0, 3600.0)  # 97%, 100 s left
        self.assertEqual(lib.progress(video(2)), 1.0)

    def test_playing_again_keeps_the_position(self):
        lib = self.library()
        lib.played(video(1))
        lib.note_position(video(1), 100.0, 600.0)
        lib.played(video(2))
        lib.played(dict(video(1), title="new title"))
        self.assertEqual(lib.history()[0]["title"], "new title")
        self.assertEqual(lib.resume_at(video(1)), 100.0)

    def test_positions_of_videos_not_played_are_ignored(self):
        lib = self.library()
        lib.note_position(video(9), 100.0, 600.0)
        self.assertEqual((lib.history(), self.events), ([], []))
        self.assertIsNone(lib.progress(video(9)))

    def test_same_position_written_once(self):
        lib = self.library()
        lib.played(video(1))
        self.events.clear()
        lib.note_position(video(1), 100.0, 600.0)
        lib.note_position(video(1), 100.0, 600.0)
        self.assertEqual(self.events, ["position"])

    def test_settings(self):
        lib = self.library()
        self.assertTrue(lib.set_enabled("vk", False))
        self.assertFalse(lib.set_enabled("vk", False))
        self.assertTrue(lib.is_disabled("vk"))
        lib.set_scope("youtube")
        lib.set_scope("youtube")
        lib.set_sort("relevance")
        self.assertEqual(self.events, ["settings"] * 3)
        again = Library(self.conf, self.data)
        self.assertEqual((again.is_disabled("vk"), again.scope, again.sort),
                         (True, "youtube", "relevance"))
        self.assertTrue(again.set_enabled("vk", True))
        self.assertFalse(again.is_disabled("vk"))

    def test_shorts_hidden_unless_asked(self):
        lib = self.library()
        self.assertFalse(lib.shorts)
        lib.set_shorts(True)
        lib.set_shorts(True)
        self.assertEqual(self.events, ["settings"])
        self.assertTrue(Library(self.conf, self.data).shorts)

    def test_bad_sort_refused(self):
        with self.assertRaises(ValueError):
            self.library().set_sort("random")

    def test_returned_lists_are_copies(self):
        lib = self.library()
        lib.follow(YT)
        lib.channels().clear()
        lib.history().append(video(1))
        self.assertEqual((len(lib.channels()), lib.history()), (1, []))

    def test_config_keeps_the_keys_of_1_4(self):
        # Same keys as the files of 1.3/1.4: old installs open unchanged.
        lib = self.library()
        lib.follow(YT)
        self.assertEqual(sorted(self.stored()), ["channels", "disabled", "scope", "sort"])


if __name__ == "__main__":
    unittest.main()
