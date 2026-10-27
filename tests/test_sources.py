# SPDX-License-Identifier: GPL-2.0
"""The sources through their interface, on recorded answers (tests/replay.py;
re-record with python3 -m tests.record). yt-dlp's part of YouTube is not
here: it has no seam of ours."""
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from sources import stream_of, vkvideo, youtube
from sources import vault_in
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
                               clock=lambda: NOW, vault=vault_in(self.home, keyring=None))

    def assertAllAsked(self):
        self.assertTrue(self.http.done(), "left unasked: %s" % self.http.queue[:1])


class VKVideosTest(Dirs):
    def test_signed_in_stream_uses_the_api_files_with_its_user_agent(self):
        asked = []

        class Downloader:
            def __init__(self, options):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def process_ie_result(self, info, download):
                asked.append(info)
                assert download is False
                return info["formats"][0]
            def extract_info(self, *args, **kwargs):
                raise AssertionError("must not extract the private page as a guest")

        vk = self.vk(exchanges=[exchange("video.get", {"response": {"items": [{
            "files": {"mp4_480": "https://cdn/480.mp4", "mp4_720": "https://cdn/720.mp4",
                      "mp4_240": "file:///private", "hls": "https://cdn/master.m3u8"}}]}},
            videos="-7_1", extended=0)])
        self.assertFalse(vk.resolve_on_desktop)
        vk.user = {"token": "SECRET", "name": "Account"}
        self.assertTrue(vk.resolve_on_desktop)
        with patch.dict("sys.modules", {"yt_dlp": SimpleNamespace(YoutubeDL=Downloader)}), \
                patch.object(vkvideo, "_skip_subtitles"):
            stream = vk.stream({"id": "-7_1", "title": "Video"}, "best")
        self.assertEqual(stream, {"urls": ["https://cdn/480.mp4"],
                                  "user_agent": vkvideo.net.UA})
        self.assertEqual([f["height"] for f in asked[0]["formats"]], [480, 720])
        self.assertNotIn("SECRET", json.dumps(asked))
        self.assertAllAsked()

    def test_search_in_catalog_order_clips_marked(self):
        videos = self.vk("vk_search").search("veritasium")
        self.assertAllAsked()
        recorded = load("vk_search")[1]["answer"]["response"]
        order = [vid for sec in recorded["catalog"]["sections"] for b in sec["blocks"]
                 if b.get("data_type") in ("catalog_videos", "videos")
                 for vid in b["videos_ids"]]
        self.assertEqual([v["id"] for v in videos], list(dict.fromkeys(order)))
        clips = [v for v in videos if v.get("short")]
        self.assertEqual(len(clips), 10)
        self.assertTrue(len(videos) > len(clips))
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
        plain = [i for i in item["image"] if not i.get("with_padding") and i["width"] >= vkvideo.THUMB_MIN]
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
    def test_playlists_load_all_pages_and_keep_the_channel_owner(self):
        vk = self.vk(exchanges=[TOKEN,
            exchange("video.getAlbums", {"response": {"count": 3, "items": [
                {"id": 7, "title": "Series", "count": 45, "image": [
                    {"width": 320, "height": 180, "url": "https://c/320", "with_padding": 1},
                    {"width": 800, "height": 450, "url": "https://c/800", "with_padding": 1}]},
                {"id": 0, "title": "All"}]}},
                owner_id=-7, count=100, offset=0, extended=1),
            exchange("video.getAlbums", {"response": {"count": 3, "items": [
                {"id": 8, "title": "Other"}]}},
                owner_id=-7, count=100, offset=2, extended=1)])
        playlists = vk.playlists({"source": "vk", "id": "-7", "title": "Channel"})
        self.assertEqual([p["id"] for p in playlists], ["7", "8"])
        self.assertEqual(playlists[0], {"source": "vk", "owner_id": "-7", "id": "7",
                                        "title": "Series", "count": 45,
                                        "thumb": "https://c/%d" % (800 if vkvideo.THUMB_MIN > 320 else 320)})
        self.assertEqual(playlists[1]["thumb"], "")  # no cover: none
        self.assertAllAsked()

    def test_playlist_keeps_order_and_other_channels_and_pages_past_hidden_videos(self):
        vk = self.vk(exchanges=[TOKEN,
            exchange("video.get", {"response": {"count": 4, "groups": [
                {"id": 7, "name": "Channel"}, {"id": 8, "name": "Other"}], "items": [
                {"owner_id": -7, "id": 1, "title": "First", "date": 100},
                {"owner_id": -8, "id": 2, "title": "Second", "date": 200},
                {"owner_id": -7, "id": 3, "restriction": {"can_play": 0}}]}},
                owner_id=-7, album_id=7, count=30, offset=0, extended=1),
            exchange("video.get", {"response": {"count": 4, "items": [
                {"owner_id": -7, "id": 4, "title": "Last"}]}},
                owner_id=-7, album_id=7, count=30, offset=3, extended=1)])
        playlist = {"source": "vk", "owner_id": "-7", "id": "7", "title": "Series"}
        page = vk.playlist_videos(playlist)
        self.assertEqual([v["id"] for v in page["videos"]], ["-7_1", "-8_2"])
        self.assertEqual(page["videos"][1]["channel_id"], "-8")
        self.assertEqual(page["videos"][1]["channel"], "Other")
        self.assertEqual(page["next"], 3)
        page = vk.playlist_videos(playlist, page["next"])
        self.assertEqual([v["id"] for v in page["videos"]], ["-7_4"])
        self.assertIsNone(page["next"])
        self.assertAllAsked()

    def test_empty_playlist_stops_paging_even_if_the_count_is_stale(self):
        vk = self.vk(exchanges=[TOKEN, exchange("video.get", {"response": {
            "count": 40, "items": []}}, owner_id=-7, album_id=7, count=30,
            offset=30, extended=1)])
        self.assertEqual(vk.playlist_videos({"source": "vk", "owner_id": "-7", "id": "7"}, 30),
                         {"videos": [], "next": None})

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
    def test_import_communities_asks_for_each_counter_separately(self):
        groups = [{"id": 7, "name": "Закрытое сообщество", "is_closed": 2},
                  {"id": 8, "name": "Без видео"}]
        code = ('return [API.groups.getById({"group_ids":"7","fields":"counters"}),'
                'API.groups.getById({"group_ids":"8","fields":"counters"})];')
        vk = self.vk(exchanges=[
            exchange("groups.get", {"response": {"count": 2, "items": groups}},
                     extended=1, count=1000, fields=vkvideo.GROUP_FIELDS),
            exchange("execute", {"response": [
                {"groups": [{"id": 7, "counters": {"videos": 3}}]},
                {"groups": [{"id": 8, "counters": {"videos": 0}}]}]}, code=code)])
        vk.user = {"token": "USER", "name": "Account"}
        self.assertEqual([c["id"] for c in vk.subscriptions()], ["-7"])
        self.assertAllAsked()

    def test_import_does_not_silently_accept_a_failed_counter_request(self):
        code = 'return [API.groups.getById({"group_ids":"7","fields":"counters"})];'
        vk = self.vk(exchanges=[
            exchange("groups.get", {"response": {"count": 1, "items": [{"id": 7}]}},
                     extended=1, count=1000, fields=vkvideo.GROUP_FIELDS),
            exchange("execute", {"response": [False]}, code=code)])
        vk.user = {"token": "USER", "name": "Account"}
        with self.assertRaises(vkvideo.ApiError):
            vk.subscriptions()

    def test_import_batches_at_most_25_individual_counter_calls(self):
        import re

        groups = [{"id": i, "name": "Community %d" % i} for i in range(1, 27)]
        batches = []

        class HTTP:
            def get_json(inner, url, data, headers):
                if "/groups.get?" in url:
                    return {"response": {"count": 26, "items": groups}}
                self.assertIn("/execute?", url)
                ids = [int(i) for i in re.findall(r'"group_ids":"(\d+)"', data["code"])]
                self.assertLessEqual(len(ids), 25)
                batches.append(ids)
                return {"response": [{"groups": [{"id": i, "counters": {"videos": 1}}]}
                                     for i in ids]}

        vk = vkvideo.VKVideo(http=HTTP(), config_dir=self.home, cache_dir=self.home,
                             vault=vault_in(self.home, keyring=None))
        vk.user = {"token": "USER", "name": "Account"}
        self.assertEqual(len(vk.subscriptions()), 26)
        self.assertEqual(batches, [list(range(1, 26)), [26]])

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

    def test_phone_requests_a_code_without_expiring_the_qr(self):
        vk = self.start(self.check({"response": {"status": 5}}))
        pending = vk.pending
        self.assertEqual(vk.login_poll(), {"need_code": True})
        self.assertIs(vk.pending, pending)
        self.assertIsNone(vk.account())

    def test_validate_code_keeps_the_session_for_a_retry_then_polls(self):
        vk = self.start(
            exchange("auth.validateAuthCode", {"response": {"status": 1}},
                     validation_code="111111"),
            exchange("auth.validateAuthCode", {"response": {"status": 0}},
                     validation_code="222222"),
            self.check({"response": {"status": 2, "access_token": "USER", "user_id": 7}}),
            exchange("users.get", {"response": [{"id": 7, "first_name": "Анна"}]}))
        pending = vk.pending
        self.assertEqual(vk.login_submit("111111"), "incorrect")
        self.assertIs(vk.pending, pending)
        self.assertIsNone(vk.account())
        self.assertEqual(vk.login_submit("222222"), "accepted")
        self.assertIs(vk.pending, pending)
        self.assertEqual(vk.login_poll(), {"name": "Анна"})
        for method, params in self.http.asked:
            if method in ("auth.validateAuthCode", VK_API):
                self.assertEqual(params["auth_hash"], pending["auth_hash"])
        self.assertAllAsked()

    def test_validate_expired_session(self):
        vk = self.start(exchange("auth.validateAuthCode", {"response": {"status": 2}},
                                 validation_code="123456"))
        self.assertEqual(vk.login_submit("123456"), "expired")
        self.assertIsNone(vk.pending)

    def test_validate_unknown_status_does_not_expire_session(self):
        vk = self.start(exchange("auth.validateAuthCode", {"response": {"status": 99}},
                                 validation_code="123456"))
        with self.assertRaises(vkvideo.ApiError):
            vk.login_submit("123456")
        self.assertIsNotNone(vk.pending)

    def test_late_code_response_does_not_clear_a_new_qr(self):
        vk = self.start(exchange("auth.validateAuthCode", {"response": {"status": 2}},
                                 validation_code="123456"))
        new_pending = {"auth_hash": "NEW"}
        get_json = self.http.get_json

        def replaced(url, data, headers):
            vk.pending = new_pending  # a new QR while the old request was in flight
            return get_json(url, data, headers)

        self.http.get_json = replaced
        self.assertEqual(vk.login_submit("123456"), "expired")
        self.assertIs(vk.pending, new_pending)

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
                                vault=vault_in(self.home, keyring=None))
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
                               vault=vault_in(self.home, keyring=None))

    def test_channel_videos_from_the_feed(self):
        chan = {"source": "youtube", "id": "UCHnyfMqiRRG1u-2MsSQLbXA", "title": "Veritasium"}
        videos = self.yt("yt_channel_videos").channel_videos(chan)
        self.assertAllAsked()
        self.assertEqual(len(videos), 3)
        self.assertEqual([v["id"] for v in videos if v.get("short")],
                         ["6U6Z6pb6ewI", "j9p3Sn8ROL4"])
        for v in videos:
            self.assertEqual((v["source"], v["channel_id"], v["channel"]),
                             ("youtube", chan["id"], "Veritasium"))
            self.assertIsInstance(v["published"], float)
            self.assertEqual(v["thumb"], "https://i.ytimg.com/vi/%s/%s" % (v["id"], youtube.THUMB_NAME))
        # From the «Видео» tab; it does not list the Shorts.
        self.assertEqual([(v["id"], v["duration"]) for v in videos],
                         [("6U6Z6pb6ewI", None), ("NiCgcUTL9_Q", 34 * 60 + 32),
                          ("j9p3Sn8ROL4", None)])

    def test_without_durations_the_feed_still_comes(self):
        chan = {"source": "youtube", "id": "UCHnyfMqiRRG1u-2MsSQLbXA", "title": "Veritasium"}
        yt = self.yt()
        yt.http.queue = load("yt_channel_videos")[:1]
        feed = yt.http.request

        def request(url, *args, **kwargs):
            if "/feeds/" not in url:
                raise OSError("refused")
            return feed(url, *args, **kwargs)

        yt.http.request = request
        videos = yt.channel_videos(chan)
        self.assertEqual([v["duration"] for v in videos], [None] * 3)

    def test_durations_of_the_badges(self):
        text = ('"thumbnailBadgeViewModel":{"text":"1:02:03","badgeStyle":"X",'
                '"animationActivationTargetId":"abcdefghijk","lottieData":{"url":"u"}},'
                '"thumbnailBadgeViewModel":{"text":"LIVE",'
                '"animationActivationTargetId":"lllllllllll"}')
        self.assertEqual(youtube.durations(text), {"abcdefghijk": 3723})

    def test_a_short_found_is_marked(self):
        entry = {"id": "x", "ie_key": "Youtube", "title": "t"}
        self.assertNotIn("short", youtube._video(dict(entry, url="https://www.youtube.com/watch?v=x")))
        self.assertTrue(youtube._video(dict(entry, url="https://www.youtube.com/shorts/x"))["short"])

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
