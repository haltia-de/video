# SPDX-License-Identifier: GPL-2.0
"""The live services, to learn early that one changed what it answers:
HALTIA_LIVE=1 python3 -m unittest tests.test_live. Skipped otherwise. When
one fails, re-record (python3 -m tests.record) and see which test of
test_sources breaks."""
import os
import tempfile
import unittest

from sources import vkvideo, youtube

LIVE = os.environ.get("HALTIA_LIVE") == "1"


@unittest.skipUnless(LIVE, "HALTIA_LIVE=1 to ask the live services")
class LiveTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.vk = vkvideo.VKVideo(config_dir=tmp.name, cache_dir=tmp.name)
        self.yt = youtube.YouTube(config_dir=tmp.name)

    def assertVideos(self, videos, least=5):
        self.assertGreaterEqual(len(videos), least)
        for v in videos:
            self.assertTrue(v["id"] and v["title"] and v["thumb"], v)

    def test_vk_search(self):
        self.assertVideos(self.vk.search("новости"))

    def test_vk_home(self):
        self.assertVideos(self.vk.home()["videos"])

    def test_vk_channels(self):
        chans = self.vk.search_channels("vert dider")
        self.assertTrue(chans and chans[0]["id"].startswith("-"))
        self.assertVideos(self.vk.channel_videos(chans[0]))

    def test_vk_sign_in_code(self):
        got = self.vk.login_start()
        self.assertTrue(got["code"] and got["url"].startswith("https://"))
        self.assertIsNone(self.vk.login_poll())

    def test_youtube_feed(self):
        self.assertVideos(self.yt.channel_videos(
            {"source": "youtube", "id": "UCHnyfMqiRRG1u-2MsSQLbXA", "title": "Veritasium"}))


if __name__ == "__main__":
    unittest.main()
