# SPDX-License-Identifier: GPL-2.0
import unittest

from search import view

BOTH = [("youtube", "YouTube"), ("vk", "VK Видео")]


def v(vid, when, source="youtube"):
    return {"source": source, "id": vid, "published": when}


def ids(section):
    return [x["id"] for x in section["items"]]


class SearchViewTest(unittest.TestCase):
    def test_by_date_all_sources_in_one_section_newest_first(self):
        found = {"youtube": [v("y1", 10), v("y2", 30)], "vk": [v("k1", 20, "vk")]}
        got = view("search", "q", BOTH, found, set(), set(), "date")
        self.assertEqual(len(got["sections"]), 1)
        self.assertIsNone(got["sections"][0]["label"])
        self.assertEqual(ids(got["sections"][0]), ["y2", "k1", "y1"])
        self.assertEqual((got["note"], got["can_sort"]), ("", True))

    def test_undated_videos_go_last(self):
        found = {"youtube": [v("old", None), v("new", 5)]}
        got = view("search", "q", BOTH[:1], found, set(), set(), "date")
        self.assertEqual(ids(got["sections"][0]), ["new", "old"])

    def test_by_relevance_a_section_per_source_youtube_first(self):
        found = {"vk": [v("k1", 20, "vk")], "youtube": [v("y1", 10)]}
        got = view("search", "q", BOTH, found, set(), set(), "relevance")
        self.assertEqual([s["label"] for s in got["sections"]], ["YouTube", "VK Видео"])
        self.assertEqual(ids(got["sections"][0]), ["y1"])

    def test_one_source_needs_no_label(self):
        got = view("search", "q", BOTH[:1], {"youtube": [v("y1", 1)]}, set(), set(),
                   "relevance")
        self.assertIsNone(got["sections"][0]["label"])

    def test_a_source_that_found_nothing_has_no_section(self):
        got = view("search", "q", BOTH, {"youtube": [v("y1", 1)], "vk": []}, set(), set(),
                   "relevance")
        self.assertEqual([s["label"] for s in got["sections"]], ["YouTube"])

    def test_waiting_for_one_while_the_other_shows(self):
        got = view("search", "кот", BOTH, {"youtube": [v("y1", 1)]}, {"vk"}, set(), "date")
        self.assertEqual(got["note"], "Ищу «кот» (VK Видео)…")
        self.assertEqual(ids(got["sections"][0]), ["y1"])

    def test_waiting_for_both(self):
        got = view("search", "кот", BOTH, {}, {"youtube", "vk"}, set(), "date")
        self.assertEqual(got["note"], "Ищу «кот» (YouTube, VK Видео)…")
        self.assertEqual((got["sections"], got["can_sort"]), ([], False))

    def test_a_failed_source(self):
        got = view("search", "q", BOTH, {"youtube": [v("y1", 1)]}, set(), {"vk"}, "date")
        self.assertEqual(got["note"], "Не удалось: VK Видео — нет связи?")

    def test_waiting_and_failed_together(self):
        got = view("search", "q", BOTH, {}, {"youtube"}, {"vk"}, "date")
        self.assertEqual(got["note"], "Ищу «q» (YouTube)… Не удалось: VK Видео — нет связи?")

    def test_old_results_are_shown_while_asked_again_without_a_note(self):
        # A kept result being fetched again: no "Ищу…", it is on screen.
        got = view("search", "q", BOTH[:1], {"youtube": [v("y1", 1)]}, {"youtube"}, set(),
                   "date")
        self.assertEqual(got["note"], "")

    def test_nothing_found(self):
        got = view("search", "q", BOTH, {"youtube": [], "vk": []}, set(), set(), "date")
        self.assertEqual((got["note"], got["sections"], got["can_sort"]),
                         ("Ничего не найдено.", [], False))

    def test_channels_are_never_merged_or_sorted(self):
        found = {"youtube": [{"id": "c1"}], "vk": [{"id": "c2"}]}
        got = view("channels", "q", BOTH, found, set(), set(), "date")
        self.assertEqual([s["label"] for s in got["sections"]], ["YouTube", "VK Видео"])
        self.assertFalse(got["can_sort"])

    def test_no_channels(self):
        got = view("channels", "q", BOTH[1:], {"vk": []}, set(), set(), "date")
        self.assertEqual(got["note"], "Каналы не найдены.")


if __name__ == "__main__":
    unittest.main()
