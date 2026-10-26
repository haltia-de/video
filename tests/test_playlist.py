# SPDX-License-Identifier: GPL-2.0
import unittest

from playlist import collect, lesson_key, page


class PlaylistTest(unittest.TestCase):
    def test_all_orders_span_source_pages(self):
        videos = [{"title": "Урок %d" % i} for i in range(65, 0, -1)]
        asked = []

        def fetch(playlist, offset):
            asked.append(offset)
            return page(videos, offset)

        orders = collect(fetch, {})
        self.assertEqual(asked, [0, 30, 60])
        self.assertEqual(orders["vk"], videos)
        self.assertEqual(orders["reverse"], list(reversed(videos)))
        self.assertEqual([v["title"] for v in orders["lesson"]],
                         ["Урок %d" % i for i in range(1, 66)])
        first = page(orders["lesson"], 0)
        second = page(orders["lesson"], first["next"])
        self.assertEqual(second["videos"][0]["title"], "Урок 31")
        self.assertIsNone(page(orders["lesson"], 60)["next"])
        self.assertEqual(videos[0]["title"], "Урок 65", "the Source order is not mutated")

    def test_lesson_number_precedes_the_title_prefix(self):
        titles = ["Алгебра. Урок 10", "Геометрия. Урок №2", "01. Введение", "Без номера"]
        self.assertEqual([v["title"] for v in sorted(
            [{"title": t} for t in titles], key=lesson_key)],
            ["01. Введение", "Геометрия. Урок №2", "Алгебра. Урок 10", "Без номера"])

    def test_seasons_episodes_and_natural_fallback(self):
        titles = ["S02E01", "S01E10", "S01E2", "Курс 10", "Курс 2"]
        self.assertEqual([v["title"] for v in sorted(
            [{"title": t} for t in titles], key=lesson_key)],
            ["S01E2", "S01E10", "S02E01", "Курс 2", "Курс 10"])

    def test_numbered_titles_are_stable_and_ignore_case(self):
        a, b = {"title": "Урок 2", "id": "a"}, {"title": "урок 2", "id": "b"}
        self.assertEqual(sorted([a, b], key=lesson_key), [a, b])

    def test_training_day_precedes_reverse_source_order_and_course_version(self):
        titles = ["Fire HIIT - 1.0 День 10 Утренняя тренировка",
                  "Fire HIIT - 1.0 День 2 Основная тренировка",
                  "Fire HIIT - 1.0 День 10 Основная тренировка",
                  "Fire HIIT - 1.0 День 2 Вечерняя тренировка",
                  "Fire HIIT - 1.0 День 2 Утренняя тренировка"]
        videos = [{"title": t} for t in titles]
        orders = collect(lambda playlist, offset: {"videos": videos, "next": None}, {})
        self.assertEqual([v["title"] for v in orders["lesson"]],
                         [titles[4], titles[3], titles[1], titles[2], titles[0]])

    def test_equal_lesson_numbers_reverse_source_order_across_pages(self):
        videos = [{"title": "Урок 2. Яблоки"}, {"title": "Урок 2. Абрикосы"}]
        orders = collect(lambda playlist, offset: {"videos": videos[offset:offset + 1],
                         "next": 1 if offset == 0 else None}, {})
        self.assertEqual(orders["lesson"], list(reversed(videos)))

    def test_empty_playlist_and_filtered_source_page(self):
        calls = []

        def fetch(playlist, offset):
            calls.append(offset)
            return {"videos": [], "next": 30 if offset == 0 else None}

        self.assertEqual(collect(fetch, {}), {"vk": [], "reverse": [], "lesson": []})
        self.assertEqual(calls, [0, 30])
        self.assertIsNone(page([], 0)["next"])

    def test_failed_or_cyclic_fetch_never_returns_a_partial_order(self):
        with self.assertRaises(ValueError):
            collect(lambda playlist, offset: {"videos": [], "next": 0}, {})

        def failed(playlist, offset):
            if offset:
                raise OSError("offline")
            return {"videos": [{"title": "First"}], "next": 30}

        with self.assertRaises(OSError):
            collect(failed, {})
