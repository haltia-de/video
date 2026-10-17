# SPDX-License-Identifier: GPL-2.0
import json
import queue
import socket
import tempfile
import unittest

from player import Player, command

VIDEO = {"source": "vk", "id": "1_2", "title": "Кино"}


class FakeMpv:
    """The other end of the player's socket."""

    def __init__(self):
        self.app_end, self.end = socket.socketpair()
        self.end.settimeout(2)
        self.buf = b""

    def received(self):
        while b"\n" not in self.buf:
            self.buf += self.end.recv(65536)
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)["command"]

    def says(self, name, data):
        self.end.sendall((json.dumps({"event": "property-change", "name": name,
                                      "data": data}) + "\n").encode())

    def closes(self):
        self.end.close()


class PlayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.posted = queue.Queue()
        self.spawned = []
        self.mpvs = []
        self.states = []

    def connect(self, path, give_up):
        mpv = FakeMpv()
        self.mpvs.append(mpv)
        return mpv.app_end

    def player(self, connect=None, focus="/nonexistent"):
        p = Player(post=lambda fn, *a: self.posted.put((fn, a)),
                   spawn=self.spawned.append, connect=connect or self.connect,
                   runtime_dir=self.tmp.name, player="/usr/local/bin/b8k-vplay", focus=focus)
        p.watch(lambda state: self.states.append(dict(state) if state else None))
        return p

    def pump(self, until=lambda: False, most=50):
        """Run what the reader posted, as the GTK loop would."""
        for _ in range(most):
            try:
                fn, args = self.posted.get(timeout=2)
            except queue.Empty:
                return
            fn(*args)
            if until():
                return

    def started(self):
        p = self.player()
        p.open(VIDEO, "https://vkvideo.ru/video1_2", {"cookies": "/c.txt"})
        self.pump(until=lambda: p.sock is not None)
        mpv = self.mpvs[-1]
        observed = [mpv.received() for _ in range(3)]
        self.assertEqual([c[2] for c in observed], ["time-pos", "duration", "pause"])
        return p, mpv

    def test_open_starts_the_player_with_its_socket(self):
        p, _mpv = self.started()
        cmd = self.spawned[0]
        self.assertEqual(cmd[:4], ["/usr/local/bin/b8k-vplay", "--title", "Кино",
                                   "https://vkvideo.ru/video1_2"])
        self.assertTrue(cmd[4].startswith("input-ipc-server=" + self.tmp.name))
        self.assertEqual(cmd[5], "ytdl-raw-options=js-runtimes=quickjs,cookies=/c.txt")
        self.assertEqual(self.states[0],
                         {"video": VIDEO, "pos": None, "duration": None, "paused": False})

    def test_state_follows_mpv_to_the_second(self):
        p, mpv = self.started()
        self.states.clear()
        mpv.says("duration", 120.5)
        for pos in (1.0, 1.3, 1.9, 2.1):
            mpv.says("time-pos", pos)
        mpv.says("pause", True)
        self.pump(until=lambda: p.state["paused"])
        self.assertEqual([(s["pos"], s["duration"], s["paused"]) for s in self.states],
                         [(None, 120.5, False), (1.0, 120.5, False), (2.1, 120.5, False),
                          (2.1, 120.5, True)])

    def test_controls_go_to_mpv(self):
        p, mpv = self.started()
        p.play()
        self.assertEqual(mpv.received(), ["set_property", "pause", False])
        mpv.says("pause", True)
        self.pump(until=lambda: p.state["paused"])
        p.toggle()
        self.assertEqual(mpv.received(), ["set_property", "pause", False])
        p.seek(30)
        self.assertEqual(mpv.received(), ["seek", 30.0, "absolute"])

    def test_closing_the_window_ends_the_state(self):
        p, mpv = self.started()
        mpv.closes()
        self.pump(until=lambda: p.state is None)
        self.assertIsNone(p.state)
        self.assertIsNone(self.states[-1])

    def test_stop_quits_and_ends_at_once(self):
        p, mpv = self.started()
        p.stop()
        self.assertEqual(mpv.received(), ["quit"])
        self.assertIsNone(p.state)

    def test_a_new_video_replaces_the_one_playing(self):
        p, first = self.started()
        p.open(dict(VIDEO, id="3_4", title="Другое"), "https://vkvideo.ru/video3_4")
        self.assertEqual(first.received(), ["quit"])
        self.pump(until=lambda: p.sock is not None and len(self.mpvs) == 2)
        first.says("time-pos", 99.0)  # the old one, late: dropped
        second = self.mpvs[-1]
        second.says("time-pos", 5.0)
        self.pump(until=lambda: p.state["pos"] == 5.0)
        self.assertEqual((p.state["video"]["id"], p.state["pos"]), ("3_4", 5.0))

    def test_a_player_that_never_opens_its_socket(self):
        p = self.player(connect=lambda path, give_up: None)
        p.open(VIDEO, "u")
        self.pump(until=lambda: p.state is None)
        self.assertIsNone(p.state)

    def test_show_needs_the_shells_focus_tool(self):
        p, _mpv = self.started()
        p.show()
        self.assertEqual(len(self.spawned), 1, "no b8k-focus: nothing run")

    def test_mpv_elsewhere(self):
        cmd = command("mpv", VIDEO, "u", {"cookies": "/c"}, "/s")
        self.assertEqual(cmd, ["mpv", "--fs", "--force-media-title=Кино",
                               "--input-ipc-server=/s", "u", "--ytdl-raw-options=cookies=/c"])

    def test_no_ytdl_options_no_raw_list(self):
        cmd = command("/usr/local/bin/b8k-vplay", VIDEO, "u", {}, "/s")
        self.assertFalse(any(c.startswith("ytdl-raw-options") for c in cmd))


if __name__ == "__main__":
    unittest.main()
