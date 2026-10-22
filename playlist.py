# SPDX-License-Identifier: GPL-2.0
"""A playlist's complete ordering, apart from GTK and the Source.

Keep normal browsing paged; collect() is fetched on a worker only when
another order needs the whole playlist. Build the sorted lists there
too: sorting every time a page is drawn would waste the tablet's CPU.
"""
import re

ORDERS = (("vk", "Порядок VK"), ("reverse", "Обратный порядок"),
          ("lesson", "По номеру урока"))
NUMBER = r"[\s._:#№-]*(\d+)"
LESSON = re.compile(r"\b(?:урок|занятие|лекция|выпуск|серия|часть|lesson|episode|lecture|part)" + NUMBER)
SEASON = re.compile(r"\b(?:сезон|season)" + NUMBER)
EPISODE = re.compile(r"\bs(\d+)[\s._-]*e(\d+)\b")
LEADING = re.compile(r"^\s*(?:№\s*)?(\d+)\b")


def lesson_key(video):
    title = video.get("title", "").casefold().replace("ё", "е")
    natural = tuple((1, int(p)) if p.isdecimal() else (0, p)
                    for p in re.split(r"(\d+)", title))
    episode = EPISODE.search(title)
    if episode:
        return (0, int(episode[1]), int(episode[2]), natural)
    lesson = LESSON.search(title) or LEADING.search(title)
    if lesson:
        season = SEASON.search(title)
        return (0, int(season[1]) if season else 0, int(lesson[1]), natural)
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
    orders = {"vk": videos, "reverse": list(reversed(videos)),
              "lesson": sorted(videos, key=lesson_key)}
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
    index = next((i for i, v in enumerate(videos)
                  if (v["source"], v["id"]) == (last["source"], last["id"])), None)
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
