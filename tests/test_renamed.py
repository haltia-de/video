# SPDX-License-Identifier: GPL-2.0
import os
import tempfile
import unittest

import renamed


class MoveOldTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def put(self, rel, text="x"):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def read(self, rel):
        with open(os.path.join(self.home, rel)) as fh:
            return fh.read()

    def test_moves_once_and_leaves_links(self):
        self.put(".config/b8k/video.json", "channels")
        self.put(".config/b8k/video-vk.json", "token")
        self.put(".config/b8k/max.conf", "not ours")
        self.put(".local/share/b8k-video/history.json", "history")
        self.put(".cache/b8k-video/feed.json", "feed")
        renamed.move_old(self.home)
        for rel, text in ((".config/haltia-video/video.json", "channels"),
                          (".config/haltia-video/video-vk.json", "token"),
                          (".local/share/haltia-video/history.json", "history"),
                          (".cache/haltia-video/feed.json", "feed")):
            self.assertEqual(self.read(rel), text, rel)
            old = rel.replace("haltia-video/", "b8k/" if rel.startswith(".config") else "b8k-video/")
            self.assertEqual(self.read(old), text, "the old path still reads it")
        self.assertTrue(os.path.islink(os.path.join(self.home, ".config/b8k/video.json")))
        self.assertFalse(os.path.islink(os.path.join(self.home, ".config/b8k/max.conf")))
        self.assertEqual(os.stat(os.path.join(self.home, ".config/haltia-video")).st_mode & 0o777, 0o700)
        self.assertEqual(renamed.move_old(self.home), [], "a second start moves nothing")

    def test_never_over_the_new(self):
        self.put(".config/b8k/video.json", "old")
        self.put(".config/haltia-video/video.json", "new")
        self.assertEqual(renamed.move_old(self.home), [])
        self.assertEqual(self.read(".config/b8k/video.json"), "old")
        self.assertEqual(self.read(".config/haltia-video/video.json"), "new")

    def test_put_at_the_old_place_later(self):
        self.put(".config/b8k/video.json")
        renamed.move_old(self.home)
        self.put(".config/b8k/youtube-cookies.txt", "cookies")
        moved = renamed.move_old(self.home)
        self.assertEqual(moved, [os.path.join(self.home, ".config/haltia-video/youtube-cookies.txt")])

    def test_nothing_to_move(self):
        self.assertEqual(renamed.move_old(self.home), [])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/haltia-video")))


if __name__ == "__main__":
    unittest.main()
