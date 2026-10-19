# SPDX-License-Identifier: GPL-2.0
"""Record the scenarios of tests/fixtures from the live services, when one
of them changed what it answers: python3 -m tests.record [name ...]

Answers are trimmed (the stream addresses, statistics and tracking fields
of a video take most of a catalog answer and are never read) and tokens
are replaced, so the files stay small and hold nothing secret. Not run by
unittest. Sign-in scenarios are not recorded here: they need a phone."""
import json
import os
import sys
import tempfile

from sources import net, vkvideo, youtube
from sources.vault import Vault
from tests.replay import FIXTURES, key_params, method_of

DROP = {"files", "trailer", "timeline_thumbs", "ads", "episodes", "stats_pixels",
        "tracking_info", "track_code", "first_frame", "subtitles", "download", "likes",
        "reposts", "player", "attached_short_videos", "linked_to_playlist_marks",
        "video_lives_data", "photo_50", "photo_100", "photo_base", "action_button",
        "search_filters", "albums", "video_showcase_galleries", "next_from"}
TEXT_MAX = 200
FEED_ENTRIES = 3


def trim_images(images):
    """Of a video's dozen picture sizes, the ones that tell the choice of
    a card picture apart: a padded one, and the two smallest that are not."""
    padded = [i for i in images if i.get("with_padding")][:1]
    plain = sorted((i for i in images if not i.get("with_padding")),
                   key=lambda i: i.get("width", 0))
    big = [i for i in plain if i.get("width", 0) >= 320][:2]
    return padded + ([plain[0]] if plain and plain[0] not in big else []) + big


def trim(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DROP:
                continue
            if k == "image" and isinstance(v, list):
                v = trim_images(v)
            if k in ("access_token", "token", "super_app_token") and isinstance(v, str):
                v = "TOKEN"
            elif isinstance(v, str) and len(v) > TEXT_MAX and k not in ("url", "id"):
                v = v[:TEXT_MAX]
            out[k] = trim(v)
        return out
    if isinstance(obj, list):
        return [trim(x) for x in obj]
    return obj


def trim_feed(xml):
    """An Atom feed with only its first entries."""
    head, sep, rest = xml.partition("<entry>")
    entries = ("<entry>" + rest).split("</entry>")[:FEED_ENTRIES]
    return head + "</entry>".join(entries) + "</entry>\n</feed>\n"


class Recorder:
    def __init__(self):
        self.exchanges = []

    def get_json(self, url, data=None, headers=None):
        answer = net.get_json(url, data, headers)
        self.exchanges.append({"url": method_of(url), "params": key_params(data),
                               "answer": trim(answer)})
        return answer

    def request(self, url, data=None, headers=None, limit=net.MAX_BYTES):
        answer = net.request(url, data, headers, limit)
        self.exchanges.append({"url": method_of(url), "params": key_params(data),
                               "answer": trim_feed(answer.decode("utf-8"))})
        return answer


def vk(rec, home):
    return vkvideo.VKVideo(http=rec, config_dir=home, cache_dir=home,
                           vault=Vault(home, keyring=None))


SCENARIOS = {
    "vk_search": lambda r, h: vk(r, h).search("veritasium"),
    "vk_home": lambda r, h: vk(r, h).home(),
    "vk_search_channels": lambda r, h: vk(r, h).search_channels("vert dider"),
    "vk_find_community": lambda r, h: vk(r, h).find_channel("https://vkvideo.ru/@studio_vd"),
    "vk_find_person": lambda r, h: vk(r, h).find_channel("@durov"),
    "vk_find_nobody": lambda r, h: vk(r, h).find_channel("nosuchname_zz_qq"),
    "vk_channel_videos": lambda r, h: vk(r, h).channel_videos(
        {"source": "vk", "id": "-55155418", "title": "VERT DIDER"}),
    "vk_login_start": lambda r, h: (lambda s: (s.login_start(), s.login_poll()))(vk(r, h)),
    "yt_channel_videos": lambda r, h: youtube.YouTube(http=r, config_dir=h, vault=Vault(h, keyring=None)).channel_videos(
        {"source": "youtube", "id": "UCHnyfMqiRRG1u-2MsSQLbXA", "title": "Veritasium"}),
}


def main(names):
    os.makedirs(FIXTURES, exist_ok=True)
    for name in names or sorted(SCENARIOS):
        rec = Recorder()
        with tempfile.TemporaryDirectory() as home:  # no token kept between
            SCENARIOS[name](rec, home)
        path = os.path.join(FIXTURES, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec.exchanges, fh, ensure_ascii=False, indent=0)
        print("%s: %d exchanges, %d bytes" % (name, len(rec.exchanges), os.path.getsize(path)))


if __name__ == "__main__":
    main(sys.argv[1:])
