# SPDX-License-Identifier: GPL-2.0
"""A playlist's complete ordering, apart from GTK and the Source.

Keep normal browsing paged; collect() is fetched on a worker only when
another order needs the whole playlist. Build the sorted lists there
too: sorting every time a page is drawn would waste the tablet's CPU.
"""
import re
import secrets
from collections import Counter
from .bookmarks import key

ORDERS = (("vk", "Порядок сервиса"), ("reverse", "Обратный порядок"),
          ("lesson", "По номеру урока"), ("custom", "Мой порядок"))
NUMBER = r"[\s._:#№-]*(\d+)"
LESSON = re.compile(r"\b(?:урок|день|занятие|лекция|выпуск|серия|часть|lesson|day|episode|lecture|part)" + NUMBER)
SEASON = re.compile(r"\b(?:сезон|season)" + NUMBER)
EPISODE = re.compile(r"\bs(\d+)[\s._-]*e(\d+)\b")
LEADING = re.compile(r"^\s*(?:№\s*)?(\d+)\b")


def lesson_key(video):
    title = video.get("title", "").casefold().replace("ё", "е")
    natural = tuple((1, int(p)) if p.isdecimal() else (0, p)
                    for p in re.split(r"(\d+)", title))
    episode = EPISODE.search(title)
    if episode:
        return (0, int(episode[1]), int(episode[2]), ())
    lesson = LESSON.search(title) or LEADING.search(title)
    if lesson:
        season = SEASON.search(title)
        return (0, int(season[1]) if season else 0, int(lesson[1]), ())
    return (1, 0, 0, natural)


def collect(fetch, playlist, locations=False):
    videos, seen, offset = [], set(), 0
    pages, positions, previous = {}, {}, ()
    while True:
        if offset in seen:
            raise ValueError("playlist pagination repeated an offset")
        seen.add(offset)
        page = fetch(playlist, offset)
        if locations:
            pages[offset] = page
            for video in page["videos"]:
                positions.setdefault((video["source"], video["id"]), (offset, previous))
            previous += (offset,)
        videos.extend(page["videos"])
        offset = page["next"]
        if offset is None:
            break
    # Stable sorting supplies both criteria: lesson/day first, reverse
    # Source order within a number. Alphabetical title ties used to put
    # a main workout before a morning workout from the same day.
    reverse = list(reversed(videos))
    orders = {"vk": videos, "reverse": reverse,
              "lesson": sorted(reverse, key=lesson_key)}
    if locations:
        orders.update(_pages=pages, _locations=positions)
    return orders


def continuation(videos, saved):
    """Resolve the saved identity in today's chosen order, never an old
    page number. A disappeared video offers the first available one.
    """
    if not videos:
        return {"kind": "empty", "video": None, "index": None}
    last = saved["video"]
    index = next((i for i, v in enumerate(videos) if same_entry(v, last)), None)
    if index is None:
        return {"kind": "missing", "video": videos[0], "index": 0}
    if saved["complete"]:
        if index + 1 == len(videos):
            return {"kind": "finished", "video": videos[0], "index": 0}
        return {"kind": "next", "video": videos[index + 1], "index": index + 1}
    return {"kind": "resume", "video": videos[index], "index": index}


def page(videos, offset, count=30):
    after = offset + count
    return {"videos": videos[offset:after], "next": after if after < len(videos) else None}


def identify(orders):
    """Source order numbers identical repeats independently of other videos."""
    counts = Counter()
    videos = [dict(v) for v in orders["vk"]]
    for video in videos:
        ident = key("video", video)
        counts[ident] += 1
        video["entry_id"] = "%s#%d" % (ident, counts[ident])
    orders["vk"] = videos
    orders["reverse"] = list(reversed(videos))
    orders["lesson"] = sorted(orders["reverse"], key=lesson_key)
    orders["_entries"] = {v["entry_id"]: v for v in videos}
    orders["_groups"] = {v["entry_id"]: group(v) for v in videos}
    orders["_collection"] = secrets.token_hex(12)
    if "_pages" in orders:
        cursor = 0
        previous = ()
        orders["_entry_locations"] = {}
        for offset, p in orders["_pages"].items():
            p = dict(p, videos=videos[cursor:cursor + len(p["videos"])])
            orders["_pages"][offset] = p
            cursor += len(p["videos"])
            for i, video in enumerate(p["videos"]):
                video["source_offset"] = offset + i
                orders["_entry_locations"][video["entry_id"]] = (offset, previous)
            previous += (offset,)
    return orders


def group(video):
    value = lesson_key(video)
    return value[:3] if value[0] == 0 else value


def arranged(orders, mode, saved):
    base = orders["lesson" if mode == "lesson" else "vk"]
    if not saved:
        return base
    ranks = {ident: i for i, ident in enumerate(saved)}
    known = sorted((v for v in base if v["entry_id"] in ranks), key=lambda v: ranks[v["entry_id"]])
    result = known + [v for v in base if v["entry_id"] not in ranks]
    if mode == "lesson":
        result.sort(key=lambda v: orders["_groups"][v["entry_id"]])
    return result


def task_continuation(videos, saved, done):
    if not videos:
        return {"kind": "empty", "video": None, "index": None}
    remaining = [i for i, v in enumerate(videos) if key("video", v) not in done]
    if not remaining:
        return {"kind": "tasks_done", "video": videos[0], "index": 0}
    last = (saved or {}).get("video", {})
    index = next((i for i, v in enumerate(videos) if same_entry(v, last)), None)
    target = next((i for i in remaining if index is not None and i >= index), remaining[0])
    return {"kind": "resume" if target == index else "task_next", "video": videos[target], "index": target}


def same_entry(a, b):
    if a.get("entry_id") and b.get("entry_id"):
        return a["entry_id"] == b["entry_id"]
    return (a.get("source"), a.get("id")) == (b.get("source"), b.get("id"))


def short_title(title, playlist_title):
    """A video's title without the playlist's name it starts with: in
    «Fire HIIT 1.0» the lesson «Fire HIIT 1.0 День 3» is «День 3» (each
    card repeated the name the page shows over them)."""
    if playlist_title and title.startswith(playlist_title):
        rest = title[len(playlist_title):].lstrip(" -\u2013\u2014:|.,\u00b7")
        if rest:
            return rest
    return title
