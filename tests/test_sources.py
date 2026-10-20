# SPDX-License-Identifier: GPL-2.0
"""The sources through their interface, on recorded answers (tests/replay.py;
re-record with python3 -m tests.record). yt-dlp's part of YouTube is not
here: it has no seam of ours."""
import json
import os
import stat
import tempfile
import unittest

from sources import stream_of, vkvideo, youtube
from sources.vault import Vault
from tests.replay import Replay, exchange, load

TOKEN = load("vk_search")[0]  # the anonymous token exchange, as recorded
# The tests' time: an hour before the recorded tokens expire (each
# recording's expiry is a real date, long past by the time tests run).
NOW = min(ex["answer"]["data"]["expired_at"]
          for name in ("vk_search", "vk_home", "vk_search_channels", "vk_find_community",
                       "vk_find_person", "vk_find_nobody", "vk_channel_videos", "vk_login_start")
          for ex in load(name) if ex["url"] == TOKEN["url"]) - 3600
VK_API = "auth.checkAuthCode"


class Dirs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def vk(self, *scenarios, exchanges=()):
        self.http = Replay(*scenarios, exchanges=exchanges)
        return vkvideo.VKVideo(http=self.http, config_dir=self.home, cache_dir=self.home,
                               clock=lambda: NOW, vault=Vault(self.home, keyring=None))

    def assertAllAsked(self):
        self.assertTrue(self.http.done(), "left unasked: %s" % self.http.queue[:1])


class VKVideosTest(Dirs):
    def test_search_in_catalog_order_without_clips(self):
        videos = self.vk("vk_search").search("veritasium")
        self.assertAllAsked()
        recorded = load("vk_search")[1]["answer"]["response"]
        order = [vid for sec in recorded["catalog"]["sections"] for b in sec["blocks"]
                 if b.get("data_type") == "catalog_videos" for vid in b["videos_ids"]]
        self.assertEqual([v["id"] for v in videos], list(dict.fromkeys(order)))
        self.assertTrue(videos)
        for v in videos:
            self.assertEqual(v["source"], "vk")
            self.assertEqual(v["channel_id"], v["id"].split("_")[0])
            self.assertTrue(v["title"] and v["published"])
        self.assertTrue(all(v["channel"] for v in videos), "every owner named")

    def test_card_picture_is_the_smallest_unpadded_16_9(self):
        recorded = load("vk_search")[1]["answer"]["response"]
        video = self.vk("vk_search").search("veritasium")[0]
        item = next(e["video"] for e in recorded["catalog_videos"]
                    if "%s_%s" % (e["video"]["owner_id"], e["video"]["id"]) == video["id"])
        plain = [i for i in item["image"] if not i.get("with_padding") and i["width"] >= 320]
        self.assertEqual(video["thumb"], min(plain, key=lambda i: i["width"])["url"])

    def test_videos_that_would_not_play_are_left_out(self):
        recorded = load("vk_channel_videos")[1]["answer"]["response"]["items"]
        blocked = {"%s_%s" % (i["owner_id"], i["id"]) for i in recorded
                   if (i.get("restriction") or {}).get("can_play") == 0}
        self.assertTrue(blocked, "the recording has restricted videos")
        videos = self.vk("vk_channel_videos").channel_videos(
            {"source": "vk", "id": "-55155418", "title": "VERT DIDER"})
        self.assertEqual(len(videos), len(recorded) - len(blocked))
        self.assertFalse(blocked & {v["id"] for v in videos})
        self.assertTrue(all(v["thumb"] for v in videos))

    def test_home_is_the_popular_section(self):
        got = self.vk("vk_home").home()
        self.assertAllAsked()
        self.assertEqual(got["title"], "Популярное в VK Видео")
        self.assertGreaterEqual(len(got["videos"]), 10)

    def test_channel_videos_newest_first_and_named_after_the_channel(self):
        chan = {"source": "vk", "id": "-55155418", "title": "VERT DIDER"}
        videos = self.vk("vk_channel_videos").channel_videos(chan)
        self.assertAllAsked()
        self.assertTrue(videos)
        times = [v["published"] for v in videos]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual({(v["channel"], v["channel_id"]) for v in videos},
                         {("VERT DIDER", "-55155418")})


class VKChannelsTest(Dirs):
    def test_search_channels(self):
        chans = self.vk("vk_search_channels").search_channels("vert dider")
        self.assertAllAsked()
        self.assertTrue(chans)
        top = chans[0]
        self.assertEqual((top["id"], top["handle"]), ("-55155418", "@studio_vd"))
        self.assertIsInstance(top["subscribers"], int)
        self.assertTrue(top["thumb"] and top["description"])

    def test_a_link_finds_the_community(self):
        chan = self.vk("vk_find_community").find_channel("https://vkvideo.ru/@studio_vd")
        self.assertAllAsked()
        self.assertEqual((chan["id"], chan["title"]), ("-55155418", "VERT DIDER"))

    def test_a_link_in_channel_search_finds_it_without_searching(self):
        chans = self.vk("vk_find_community").search_channels("https://vkvideo.ru/@studio_vd")
        self.assertEqual([c["id"] for c in chans], ["-55155418"])

    def test_a_person_is_a_channel_too(self):
        chan = self.vk("vk_find_person").find_channel("@durov")
        self.assertEqual((chan["id"], chan["handle"]), ("1", "@durov"))

    def test_nobody(self):
        self.assertIsNone(self.vk("vk_find_nobody").find_channel("nosuchname_zz_qq"))

    def test_owns_its_links_only(self):
        vk = self.vk()
        self.assertTrue(vk.owns("https://vk.com/club1"))
        self.assertTrue(vk.owns("vkvideo.ru/@x"))
        self.assertFalse(vk.owns("@x"))
        self.assertFalse(vk.owns("https://www.youtube.com/@x"))


class VKTokenTest(Dirs):
    def test_anonymous_token_is_kept_private_and_reused(self):
        self.vk("vk_find_nobody").find_channel("nosuchname_zz_qq")
        path = os.path.join(self.home, "vk-anonym.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as fh:
            kept = json.load(fh)
        kept["until"] = 4102444800  # 2100: not stale whatever the clock says
        with open(path, "w") as fh:
            json.dump(kept, fh)
        # A new start asks no token: the recording without its token exchange.
        self.vk(exchanges=load("vk_find_nobody")[1:]).find_channel("nosuchname_zz_qq")
        self.assertAllAsked()

    def test_a_stale_token_is_replaced_once(self):
        stale = exchange("utils.resolveScreenName",
                         {"error": {"error_code": 5, "error_msg": "User authorization failed"}},
                         screen_name="nosuchname_zz_qq")
        nobody = load("vk_find_nobody")
        self.vk(exchanges=[nobody[0], stale, nobody[0], nobody[1]]).find_channel(
            "nosuchname_zz_qq")
        self.assertAllAsked()

    def test_other_errors_reach_the_caller(self):
        err = exchange("utils.resolveScreenName",
                       {"error": {"error_code": 6, "error_msg": "Too many requests"}},
                       screen_name="x")
        vk = self.vk(exchanges=[TOKEN, err])
        with self.assertRaises(vkvideo.ApiError) as cm:
            vk.find_channel("x")
        self.assertEqual(cm.exception.code, 6)


class VKSignInTest(Dirs):
    def start(self, *after):
        rec = load("vk_login_start")
        vk = self.vk(exchanges=rec[:2] + list(after))
        got = vk.login_start()
        self.assertEqual(got["code"], rec[1]["answer"]["response"]["auth_code"])
        self.assertTrue(got["url"].startswith("https://qr.vk.ru/"))
        return vk

    def check(self, answer):
        return exchange(VK_API, answer)

    def test_waiting(self):
        vk = self.start(load("vk_login_start")[2])
        self.assertIsNone(vk.login_poll())
        self.assertIsNone(vk.account())

    def test_expired_code(self):
        # Recorded by hand on 17.10.2026: status 4 three minutes on.
        vk = self.start(self.check({"response": {"status": 4}}))
        with self.assertRaises(vkvideo.ApiError):
            vk.login_poll()
        with self.assertRaises(vkvideo.ApiError):
            vk.login_poll()  # nothing pending any more

    # ASSUMPTION, not yet seen live: a confirmed code answers status 2 with
    # the person's access_token and user_id.
    def test_confirmed(self):
        me = exchange("users.get", {"response": [{"id": 7, "first_name": "Анна",
                                                  "last_name": "Котова"}]})
        vk = self.start(self.check({"response": {"status": 2, "access_token": "USER",
                                                 "user_id": 7}}), me)
        self.assertEqual(vk.login_poll(), {"name": "Анна Котова"})
        self.assertEqual(vk.account(), {"name": "Анна Котова"})
        path = os.path.join(self.home, "video-vk.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(self.http.asked[-1][1]["access_token"], "USER")
        # A new start is signed in, and asks as the person.
        again = vkvideo.VKVideo(http=Replay(exchanges=load("vk_find_nobody")[1:]), clock=lambda: NOW,
                                config_dir=self.home, cache_dir=self.home,
                                vault=Vault(self.home, keyring=None))
        self.assertEqual(again.account(), {"name": "Анна Котова"})
        again.find_channel("nosuchname_zz_qq")
        self.assertEqual(again.http.asked[0][1]["access_token"], "USER")

    def test_revoked_token_signs_out_and_goes_on_as_a_guest(self):
        with open(os.path.join(self.home, "video-vk.json"), "w") as fh:
            json.dump({"token": "USER", "id": 7, "name": "Анна"}, fh)
        revoked = exchange("utils.resolveScreenName",
                           {"error": {"error_code": 5, "error_msg": "User authorization failed"}},
                           screen_name="nosuchname_zz_qq")
        nobody = load("vk_find_nobody")
        vk = self.vk(exchanges=[revoked, nobody[0], nobody[1]])
        self.assertIsNone(vk.find_channel("nosuchname_zz_qq"))
        self.assertAllAsked()
        self.assertIsNone(vk.account())
        self.assertFalse(os.path.exists(os.path.join(self.home, "video-vk.json")))

    def test_logout(self):
        with open(os.path.join(self.home, "video-vk.json"), "w") as fh:
            json.dump({"token": "USER", "id": 7, "name": "Анна"}, fh)
        vk = self.vk()
        self.assertEqual(vk.account(), {"name": "Анна"})
        vk.logout()
        self.assertIsNone(vk.account())


class YouTubeFeedTest(Dirs):
    def yt(self, *scenarios):
        self.http = Replay(*scenarios)
        return youtube.YouTube(http=self.http, config_dir=self.home,
                               vault=Vault(self.home, keyring=None))

    def test_channel_videos_from_the_feed(self):
        chan = {"source": "youtube", "id": "UCHnyfMqiRRG1u-2MsSQLbXA", "title": "Veritasium"}
        videos = self.yt("yt_channel_videos").channel_videos(chan)
        self.assertAllAsked()
        self.assertEqual(len(videos), 3)
        for v in videos:
            self.assertEqual((v["source"], v["channel_id"], v["channel"]),
                             ("youtube", chan["id"], "Veritasium"))
            self.assertIsInstance(v["published"], float)
            self.assertEqual(v["thumb"], "https://i.ytimg.com/vi/%s/mqdefault.jpg" % v["id"])
            self.assertIsNone(v["duration"])

    def test_owns(self):
        yt = self.yt()
        self.assertTrue(yt.owns("https://www.youtube.com/@x"))
        self.assertTrue(yt.owns("UC" + "a" * 22))
        self.assertFalse(yt.owns("https://vk.com/x"))

    def test_signed_out_without_the_cookies_file(self):
        yt = self.yt()
        self.assertIsNone(yt.account())
        self.assertEqual(yt.ytdl_options(), {})
        self.assertIsNone(yt.home())
        self.assertIn(os.path.join(self.home, "youtube-cookies.txt").replace(
            os.path.expanduser("~"), "~"), yt.login_hint)

    def test_signed_in_hands_the_cookies_to_the_player(self):
        yt = self.yt()
        cookies = os.path.join(self.home, "youtube-cookies.txt")
        open(cookies, "w").close()
        with open(os.path.join(self.home, "video-youtube.json"), "w") as fh:
            json.dump({"name": "аккаунт Google"}, fh)
        self.assertEqual(yt.account(), {"name": "аккаунт Google"})
        self.assertEqual(yt.ytdl_options(), {"cookies": cookies})
        yt.logout()
        self.assertFalse(os.path.exists(cookies))
        self.assertIsNone(yt.account())


class StreamTest(unittest.TestCase):
    def test_what_the_player_gets(self):
        self.assertEqual(
            stream_of({"requested_formats": [
                {"url": "https://v", "http_headers": {"User-Agent": "UA"}},
                {"url": "https://a"}]}),
            {"urls": ["https://v", "https://a"], "user_agent": "UA"})
        self.assertEqual(stream_of({"url": "https://both"})["urls"], ["https://both"])

    def test_only_web_streams(self):
        for url in ("edl://x", "file:///etc/passwd", "av://lavfi:x", None, ""):
            with self.assertRaises(ValueError):
                stream_of({"id": "x", "requested_formats": [{"url": "https://v"}, {"url": url}]})


if __name__ == "__main__":
    unittest.main()
