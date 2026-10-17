# SPDX-License-Identifier: GPL-2.0
import json
import os
import tempfile
import unittest

from kept import Kept


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Runner:
    """run(func, *args, done=...) that holds the job until finish()."""

    def __init__(self):
        self.jobs = []

    def __call__(self, func, *args, done):
        self.jobs.append((func, args, done))

    def finish(self, fail=None):
        func, args, done = self.jobs.pop(0)
        if fail:
            done(None, fail)
        else:
            done(func(*args), None)


class KeptTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.run = Runner()
        self.fetched = []
        self.told = []

    def fetch(self, key, *args):
        self.fetched.append((key,) + args)
        return "%s@%d" % (key, self.clock.now)

    def kept(self, **kw):
        k = Kept(self.fetch, ttl=60, run=self.run, clock=self.clock, **kw)
        k.watch(lambda key, error: self.told.append((key, error)))
        return k

    def test_first_get_fetches_and_tells(self):
        k = self.kept()
        self.assertIsNone(k.get("a"))
        self.assertTrue(k.is_loading("a"))
        self.run.finish()
        self.assertEqual(k.get("a"), "a@1000")
        self.assertEqual(self.told, [("a", None)])
        self.assertFalse(k.is_loading("a"))

    def test_one_fetch_per_key_at_a_time(self):
        k = self.kept()
        k.get("a")
        k.get("a")
        k.get("b")
        self.assertEqual(len(self.run.jobs), 2)

    def test_fresh_is_not_fetched_old_is_shown_and_fetched(self):
        k = self.kept()
        k.get("a")
        self.run.finish()
        self.clock.now += 59
        self.assertEqual(k.get("a"), "a@1000")
        self.assertEqual(self.run.jobs, [])
        self.clock.now += 1
        self.assertEqual(k.get("a"), "a@1000")  # still shown while fetched again
        self.run.finish()
        self.assertEqual(k.get("a"), "a@1060")

    def test_a_failure_keeps_the_old_value_and_waits_before_retrying(self):
        k = self.kept(retry=120)
        k.get("a")
        self.run.finish()
        self.clock.now += 60
        k.get("a")
        self.run.finish(fail=OSError("down"))
        self.assertEqual(k.get("a"), "a@1000")
        self.assertTrue(k.has_failed("a"))
        self.assertEqual(self.run.jobs, [], "no retry within two minutes")
        self.assertIsInstance(self.told[-1][1], OSError)
        self.clock.now += 120
        k.get("a")
        self.run.finish()
        self.assertFalse(k.has_failed("a"))

    def test_invalidate_fetches_again_even_after_a_failure(self):
        k = self.kept()
        k.get("a")
        self.run.finish(fail=OSError())
        k.invalidate("a")
        k.get("a")
        self.assertEqual(len(self.run.jobs), 1)

    def test_invalidated_while_fetched_counts_as_old_on_arrival(self):
        # Followed a channel while the feed was being fetched without it.
        k = self.kept()
        k.get("feed")
        k.invalidate("feed")
        self.run.finish()
        self.assertEqual(k.get("feed"), "feed@1000", "shown all the same")
        self.assertEqual(len(self.run.jobs), 1, "and fetched again at once")

    def test_invalidate_all(self):
        k = self.kept()
        for key in "ab":
            k.get(key)
            self.run.finish()
        k.invalidate()
        k.get("a")
        k.get("b")
        self.assertEqual(len(self.run.jobs), 2)

    def test_forget(self):
        k = self.kept()
        k.get("a")
        self.run.finish()
        k.forget("a")
        self.assertIsNone(k.get("a"))
        self.run.finish()
        k.get("b")
        self.run.finish()
        k.forget()
        self.assertEqual(k.entries, {})

    def test_prepare_runs_on_the_callers_side(self):
        k = self.kept(prepare=lambda key: (key.upper(),))
        k.get("a")
        self.run.finish()
        self.assertEqual(self.fetched, [("a", "A")])

    def test_empty_results_are_kept_too(self):
        k = Kept(lambda key: [], ttl=60, run=self.run, clock=self.clock)
        k.get("q")
        self.run.finish()
        self.assertEqual(k.get("q"), [])
        self.assertEqual(self.run.jobs, [])

    def test_kept_on_disk_across_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "feed.json")
            k = self.kept(path=path)
            k.get("feed")
            self.run.finish()
            again = Kept(self.fetch, ttl=60, run=self.run, clock=self.clock, path=path)
            self.assertEqual(again.get("feed"), "feed@1000")
            self.assertEqual(self.run.jobs, [], "fresh from disk: not fetched")

    def test_an_old_cache_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "feed.json")
            with open(path, "w") as fh:
                json.dump({"videos": [1, 2], "time": 999}, fh)  # 1.3's feed.json
            k = self.kept(path=path)
            self.assertIsNone(k.get("feed"))
            self.assertEqual(len(self.run.jobs), 1)


if __name__ == "__main__":
    unittest.main()
