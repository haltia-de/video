# SPDX-License-Identifier: GPL-2.0
"""How long the GTK thread is busy when big pages open, in a real window.

    python3 -m tests.bench_pages [scenario ...]   (from the repo root, under a display)

A made-up source, 300 JPEGs of 640x360 in a throwaway picture cache, the
window 1880x1200 (the P11's beside the shell's panel; BENCH_W, BENCH_H).
A 4 ms timer on the default priority, the one input waits behind, tells
how long a tap would wait: «stall» is its longest gap, «p95» its 95th
percentile until the page is done (no list being made, no picture on its
way). «make» is the time in LazyList making widgets, «layout» and «frame»
GTK's (frame: before-paint to after-paint). The pictures start cold
(decoded from the disk cache) unless BENCH_WARM is set.

What 2.12 aims at on the P11 for grid-300, bookmarks-200 and channels-80:
the first frame under 150 ms, no stall over 100 ms, p95 under 50 ms. A
PC's figures move with its load: compare runs on one machine.
"""
import hashlib
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="haltia-video-bench-")
for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
    os.environ[var] = os.path.join(TMP, var.lower())
    os.makedirs(os.environ[var])
sys.path.insert(0, str(REPO))

app = runpy.run_path(str(REPO / "haltia-video"), run_name="bench_pages")
Gtk, GLib, Gio = app["Gtk"], app["GLib"], app["Gio"]
import lazy_list  # noqa: E402
from library import Library  # noqa: E402

CACHE = app["CACHE"]
os.makedirs(CACHE, exist_ok=True)


def pictures(n):
    """n different JPEGs in the picture cache, under made-up URLs."""
    from PIL import Image, ImageDraw
    urls = []
    for i in range(n):
        url = "https://pictures.example/%d.jpg" % i
        path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".jpg")
        im = Image.new("RGB", (640, 360), ((i * 37) % 255, (i * 91) % 255, (i * 13) % 255))
        draw = ImageDraw.Draw(im)
        for k in range(0, 640, 40):
            draw.line((k, 0, 640 - k, 360), fill=(255 - k % 255, k % 255, 120), width=3)
        im.save(path, quality=85)
        urls.append(url)
    return urls


PICTURES = pictures(300)


def videos(n, prefix):
    return [{"source": "vk", "id": "-7_%s%d" % (prefix, i),
             "title": "Урок %d: довольно длинное название видео, чтобы оно переносилось на вторую строку" % i,
             "channel": "Канал %d" % (i % 9), "channel_id": "-%d" % (i % 9 + 1),
             "thumb": PICTURES[i % len(PICTURES)], "duration": 600 + i,
             "published": time.time() - i * 3600} for i in range(n)]


PLAYLIST = videos(300, "p")
source = SimpleNamespace(id="vk", label="VK Видео", home=lambda: None,
                         playlist_videos=lambda _p, offset=0: {
                             "videos": PLAYLIST[offset:offset + 30],
                             "next": offset + 30 if offset + 30 < len(PLAYLIST) else None},
                         playlists=lambda _c: [], channel_videos=lambda _c: videos(60, "c"),
                         find_channel=lambda _id: None)

Video = app["Video"]
library_dir = os.path.join(TMP, "library")
os.makedirs(library_dir)
with patch.object(app["sources"], "load", return_value={"vk": source}), \
        patch.dict(Video.__init__.__globals__, {"Library": lambda: Library(library_dir, library_dir),
                                                "FEED_CACHE": TMP + "/feed.json",
                                                "HOME_CACHE": TMP + "/home.json"}):
    view = Video()
view.set_application_id("io.github.haltia_de.Video.Bench%d" % os.getpid())
view.set_flags(Gio.ApplicationFlags.NON_UNIQUE)  # beside a Видео already running
view.register(None)
jobs = []
view.work = lambda fn, *args, done: jobs.append((fn, args, done))
view.reset_channel_data()
view.do_activate()
view.player = SimpleNamespace(state=None, stop=lambda: None)
view.win.resize(int(os.environ.get("BENCH_W", "1880")), int(os.environ.get("BENCH_H", "1200")))

gaps = []
last = [time.monotonic()]


def tick():
    now = time.monotonic()
    gaps.append(now - last[0])
    last[0] = now
    return True


GLib.timeout_add(4, tick)


def timer():
    return {"t": 0.0, "n": 0, "max": 0.0, "start": 0.0}


def timed(acc):
    return (lambda *_a: acc.update(start=time.monotonic()),
            lambda *_a: acc.update(t=acc["t"] + time.monotonic() - acc["start"], n=acc["n"] + 1,
                                   max=max(acc["max"], time.monotonic() - acc["start"])))


layout, frame, make = timer(), timer(), timer()
made = lazy_list.LazyList._add


def add(self, n):
    start, end = timed(make)
    start()
    made(self, n)
    end()


lazy_list.LazyList._add = add


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while jobs:
            fn, args, done = jobs.pop(0)
            try:
                value, error = fn(*args), None
            except Exception as exc:
                value, error = None, exc
            done(value, error)
        if not GLib.MainContext.default().iteration(False):
            time.sleep(0.001)


def until_done(limit=20):
    """Until no picture is on its way and the GTK thread has been idle a while."""
    start = time.monotonic()
    quiet = 0
    while time.monotonic() - start < limit:
        pump(0.05)
        busy = bool(view.thumbs.waiting) or max(gaps[-10:] or [0]) > 0.03
        quiet = 0 if busy else quiet + 1
        if quiet >= 6:
            break
    return time.monotonic() - start


pump(1.0)
clock = view.win.get_frame_clock()
clock.connect("layout", timed(layout)[0])
clock.connect_after("layout", timed(layout)[1])
clock.connect("before-paint", timed(frame)[0])
clock.connect_after("after-paint", timed(frame)[1])


def measure(name, scenario):
    prepare, action = scenario
    prepare()
    pump(0.3)
    gaps.clear()
    for acc in (layout, frame, make):
        acc.update(timer())
    if not os.environ.get("BENCH_WARM"):
        view.thumbs.kept = app["pictures"].Recent(app["PIXBUFS_BYTES"])
    last[0] = start = time.monotonic()
    action()
    first = time.monotonic() - start
    total = until_done() + first
    g = sorted(gaps) or [0]
    print("%-16s first %4.0f ms | done %4.1f s | stall %4.0f ms | p95 %3.0f ms | "
          "make %4.2f s max %3.0f ms | layout %4.2f s max %3.0f ms | frame %4.2f s max %3.0f ms" % (
              name, first * 1000, total, g[-1] * 1000, g[int(len(g) * .95)] * 1000,
              make["t"], make["max"] * 1000, layout["t"], layout["max"] * 1000,
              frame["t"], frame["max"] * 1000))
    sys.stdout.flush()


chan = {"source": "vk", "id": "-7", "title": "Канал"}
playlist = dict(chan, owner_id="-7", id="7", title="Плейлист")


def history():
    for video in videos(100, "h")[::-1]:
        view.library.played(video)


def bookmarks():
    for video in videos(200, "b"):
        view.core.command("bookmark_add", {"kind": "video", "entity": video})


def channels():
    for i in range(80):
        view.library.follow({"source": "vk", "id": "-%d" % (100 + i), "title": "Канал номер %d" % i,
                             "thumb": PICTURES[i], "description": "Описание канала " * 8,
                             "subscribers": 12345 * i, "handle": "@chan%d" % i})


def nothing():
    pass


def grid_of(n):
    def show():
        view.open_sub({"kind": "bench"}, "Сетка")
        view.set_content(view.grid(videos(n, "g%d" % n)))
    return show


def history_left():
    history()
    view.show_page("history")
    until_done()
    view.show_page("home")


SCENARIOS = {
    "grid-48": (nothing, grid_of(48)),
    "grid-300": (nothing, grid_of(300)),
    "history-100": (history, lambda: view.show_page("history")),
    "history-again": (history_left, lambda: view.tab_clicked("history")),
    "history-redraw": (lambda: (history(), view.show_page("history"), until_done()),
                       lambda: view.library_changed("position")),
    "playlist": (nothing, lambda: view.show_playlist(playlist, chan)),
    "editor": (nothing, lambda: view.show_playlist_editor(playlist, chan, "custom", return_order="vk")),
    "bookmarks-200": (bookmarks, lambda: view.show_page("bookmarks")),
    "channels-80": (channels, lambda: view.show_page("channels")),
}

if __name__ == "__main__":
    for name in sys.argv[1:] or SCENARIOS:
        measure(name, SCENARIOS[name])
        view.show_page("home")
        pump(0.5)
