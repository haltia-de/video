# SPDX-License-Identifier: GPL-2.0
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sources import vault_in
from sources.vkvideo import ApiError, VKVideo
from sources.youtube import YouTube
from tests.replay import Replay, exchange


class VKBookmarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def source(self, exchanges):
        self.http = Replay(exchanges=exchanges)
        src = VKVideo(http=self.http, config_dir=self.tmp.name, cache_dir=self.tmp.name,
                      vault=vault_in(self.tmp.name, keyring=None))
        src.user = {"id": 1, "name": "Account", "token": "SECRET"}
        return src

    def test_live_tv_token_unavailable_shape(self):
        exchanges = [exchange(method, {"error": {"error_code": 3,
                                "error_msg": "Unknown method passed"}}, count=1, **params)
                     for method, params in (("fave.get", {"item_type": "video"}),
                                            ("fave.getPages", {"type": "groups"}))]
        client = self.source(exchanges).bookmark_client()
        self.assertEqual(client.capabilities(), [])
        self.assertTrue(self.http.done())

    def test_captured_account_cannot_write_after_logout_or_switch(self):
        src = self.source([])
        client = src.bookmark_client()
        src.user = dict(src.user, id=2)
        with self.assertRaises(ApiError):
            client.write("channel", {"id": "-7"}, True)
        self.assertEqual(self.http.asked, [])

    def test_write_video_and_channel_are_faves_not_subscriptions(self):
        src = self.source([
            exchange("fave.addVideo", {"response": 1}, owner_id=-7, id=1),
            exchange("fave.removeVideo", {"response": 1}, owner_id=-7, id=1),
            exchange("fave.addPage", {"response": 1}, group_id=7),
            exchange("fave.removePage", {"response": 1}, user_id=7)])
        client = src.bookmark_client()
        client.write("video", {"id": "-7_1"}, True)
        client.write("video", {"id": "-7_1"}, False)
        client.write("channel", {"id": "-7"}, True)
        client.write("channel", {"id": "7"}, False)
        with self.assertRaises(ValueError):
            client.write("playlist", {"id": "7"}, True)
        self.assertTrue(self.http.done())

    def test_expired_token_does_not_fall_back_to_guest(self):
        src = self.source([exchange("fave.get", {"error": {"error_code": 5}},
                                   item_type="video", count=1)])
        with self.assertRaises(ApiError):
            src.bookmark_client().capabilities()
        self.assertEqual(len(self.http.asked), 1)
        self.assertIsNotNone(src.user)

    def test_incomplete_remote_read_must_not_be_interpreted_as_deleted(self):
        src = self.source([exchange("fave.get", {"response": {"count": 1, "items": []}},
                                   count=100, offset=0, item_type="video", extended=1)])
        with self.assertRaises(ValueError):
            src.bookmark_client().read(["video"])

    def test_restricted_remote_video_keeps_its_identity(self):
        src = self.source([exchange("fave.get", {"response": {"count": 1, "items": [{
            "type": "video", "video": {"owner_id": -7, "id": 1,
                                        "restriction": {"can_play": 0}}}]}},
                                   count=100, offset=0, item_type="video", extended=1)])
        remote = src.bookmark_client().read(["video"])
        self.assertEqual(remote[0][1]["id"], "-7_1")
        self.assertTrue(remote[0][1]["unavailable"])

    def test_video_link_resolves_metadata(self):
        src = self.source([exchange("video.get", {"response": {"items": [{
            "owner_id": -7, "id": 1, "title": "Video"}]}}, videos="-7_1", extended=1)])
        kind, obj = src.resolve_bookmark("https://vkvideo.ru/video-7_1")
        self.assertEqual((kind, obj["id"]), ("video", "-7_1"))
        with self.assertRaises(ValueError):
            src.resolve_bookmark("https://vkvideo.ru.attacker.org/video-7_1")


class YouTubeBookmarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = YouTube(config_dir=self.tmp.name, vault=vault_in(self.tmp.name, keyring=None))

    def downloader(self, answer):
        class Downloader:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def extract_info(self, *args, **kwargs):
                return answer
        return Downloader()

    def test_video_with_list_parameter_is_not_a_playlist(self):
        with patch.object(self.src, "_ydl", return_value=self.downloader(
                {"id": "abcd", "title": "Video"})):
            kind, obj = self.src.resolve_bookmark("https://www.youtube.com/watch?v=abcd&list=PL1")
        self.assertEqual((kind, obj["id"]), ("video", "abcd"))

    def test_playlist_bookmark_and_paginated_current_contents(self):
        with patch.object(self.src, "_ydl", return_value=self.downloader(
                {"id": "PL1", "title": "Course", "channel_id": "UC1"})):
            kind, obj = self.src.resolve_bookmark("https://www.youtube.com/playlist?list=PL1")
        self.assertEqual((kind, obj["id"]), ("playlist", "PL1"))
        entries = [{"id": str(i), "title": str(i)} for i in range(31)]
        with patch.object(self.src, "_ydl", return_value=self.downloader({"entries": entries})) as ydl:
            page = self.src.playlist_videos(obj, offset=30)
        self.assertEqual(len(page["videos"]), 30)
        self.assertEqual(page["next"], 60)
        self.assertEqual(ydl.call_args.args[0]["playliststart"], 31)

    def test_unrelated_host_rejected(self):
        with self.assertRaises(ValueError):
            self.src.resolve_bookmark("https://youtube.com.attacker.org/playlist?list=PL1")
