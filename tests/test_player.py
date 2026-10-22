# SPDX-License-Identifier: GPL-2.0
import json
import queue
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from player import VPLAY_FORMAT, Player, command, connect_unix, edl, find_player

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
        try:
            self.end.sendall((json.dumps({"event": "property-change", "name": name,
                                          "data": data}) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            pass  # A player whose socket was closed cannot send a late event.

    def closes(self):
        self.end.close()


class FakeMpvTest(unittest.TestCase):
    def test_late_events_tolerate_a_closed_player(self):
        mpv = FakeMpv.__new__(FakeMpv)
        for error in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error=error):
                mpv.end = Mock()
                mpv.end.sendall.side_effect = error
                mpv.says("time-pos", 99.0)
                mpv.end.sendall.assert_called_once()


class FakeProc:
    """What spawn gives back: the player's process."""

    def __init__(self, cmd):
        self.cmd = cmd
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True


class PlayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.posted = queue.Queue()
        self.spawned = []
        self.mpvs = []
        self.states = []

    def connect(self, path, give_up, alive):
        mpv = FakeMpv()
        self.mpvs.append(mpv)
        return mpv.app_end

    def player(self, connect=None, focus="/nonexistent"):
        p = Player(post=lambda fn, *a: self.posted.put((fn, a)),
                   spawn=self.spawned.append, connect=connect or self.connect,
                   runtime_dir=self.tmp.name, player="/usr/bin/haltia-vplay", focus=focus)
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

    def started(self, **kw):
        p = self.player(**kw)
        p.open(VIDEO, "https://vkvideo.ru/video1_2", {"cookies": "/c.txt"})
        self.pump(until=lambda: p.sock is not None)
        mpv = self.mpvs[-1]
        observed = [mpv.received() for _ in range(3)]
        self.assertEqual([c[2] for c in observed], ["time-pos", "duration", "pause"])
        return p, mpv

    def test_open_starts_the_player_with_its_socket(self):
        p, _mpv = self.started()
        cmd = self.spawned[0]
        self.assertEqual(cmd[:4], ["/usr/bin/haltia-vplay", "--title", "Кино",
                                   "https://vkvideo.ru/video1_2"])
        self.assertTrue(cmd[4].startswith("input-ipc-server=" + self.tmp.name))
        self.assertEqual(cmd[5], "ytdl-raw-options=js-runtimes=quickjs,cookies=/c.txt")
        self.assertEqual(self.states[0], {"video": VIDEO, "pos": None, "duration": None,
                                          "paused": False, "start": None})

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
        p = self.player(connect=lambda path, give_up, alive: None)
        procs = []
        p.spawn = lambda cmd: procs.append(FakeProc(cmd)) or procs[-1]
        p.open(VIDEO, "u")
        self.pump(until=lambda: p.state is None)
        self.assertIsNone(p.state)
        self.assertTrue(procs[0].terminated, "no bar, so no player either")

    def test_a_player_still_starting_is_ended_by_the_next(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        p = self.player(connect=lambda path, give_up, alive: gate.wait(2) and None)
        procs = []
        p.spawn = lambda cmd: procs.append(FakeProc(cmd)) or procs[-1]
        p.open(VIDEO, "u")
        p.open(dict(VIDEO, id="3_4"), "u2")
        self.assertEqual([pr.terminated for pr in procs], [True, False])
        p.stop()
        self.assertTrue(procs[1].terminated)

    def test_a_late_connection_is_told_to_quit(self):
        gate = threading.Event()

        def connect(path, give_up, alive):
            gate.wait(2)
            return self.connect(path, give_up, alive)

        p = self.player(connect=connect)
        p.open(VIDEO, "u")
        p.stop()
        gate.set()
        self.pump(most=1)  # _connected, for a run already stopped
        seen = []
        while ["quit"] not in seen:  # after the reader's observe_property, maybe
            seen.append(self.mpvs[0].received())
        self.assertIsNone(p.sock)

    def test_a_player_that_exited_is_not_waited_for(self):
        start = time.time()
        self.assertIsNone(connect_unix(self.tmp.name + "/none.sock", start + 30,
                                       lambda: False))
        self.assertLess(time.time() - start, 1)

    def test_show_needs_the_shells_focus_tool(self):
        p, _mpv = self.started()
        p.show()
        self.assertEqual(len(self.spawned), 1, "no haltia-focus: nothing run")

    def test_show_brings_the_player_window(self):
        p, _mpv = self.started(focus="/bin/true")
        p.show()
        self.assertEqual(self.spawned[-1], ["/bin/true", "activate", "haltia-vplay"])

    def test_vplay_where_installed_else_mpv(self):
        def which(*on_path):
            return lambda name: "/bin/" + name if name in on_path else None
        self.assertEqual(find_player(which("haltia-vplay", "mpv")), "/bin/haltia-vplay")
        self.assertEqual(find_player(which("b8k-vplay", "mpv")), "/bin/b8k-vplay")
        self.assertEqual(find_player(which("mpv")), "/bin/mpv")
        self.assertIsNone(find_player(which()))

    def test_no_player_at_all(self):
        p = Player(post=lambda fn, *a: None, spawn=self.spawned.append, runtime_dir=self.tmp.name)
        p.player = None
        with self.assertRaises(OSError):
            p.open(VIDEO, "u")
        self.assertEqual(self.spawned, [])
        self.assertIsNone(p.state)

    def test_the_old_name_is_the_same_player(self):
        cmd = command("/usr/bin/b8k-vplay", VIDEO, "u", {}, "/s")
        self.assertEqual(cmd[:4], ["/usr/bin/b8k-vplay", "--title", "Кино", "u"])

    def test_sockets_live_where_only_this_user_reaches(self):
        import os
        import stat
        from player import private_dir
        mine = private_dir(self.tmp.name)
        self.assertEqual(mine, os.path.join(self.tmp.name, "haltia-video"))
        self.assertEqual(stat.S_IMODE(os.stat(mine).st_mode), 0o700)
        # Someone's link in its place, or a directory open to others: not used.
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(os.path.join(other, "elsewhere"))
        os.symlink(os.path.join(other, "elsewhere"), os.path.join(other, "haltia-video"))
        self.assertNotEqual(private_dir(other), os.path.join(other, "haltia-video"))
        opened = os.path.join(self.tmp.name, "opened")
        os.makedirs(os.path.join(opened, "haltia-video"))
        os.chmod(os.path.join(opened, "haltia-video"), 0o777)
        self.assertNotEqual(private_dir(opened), os.path.join(opened, "haltia-video"))
        # No runtime directory at all: a new private one, not /tmp/haltia-video.
        fresh = private_dir(None)
        self.addCleanup(os.rmdir, fresh)
        self.assertEqual(stat.S_IMODE(os.stat(fresh).st_mode), 0o700)

    def test_socket_names_are_not_guessable(self):
        p, _mpv = self.started()
        first = self.spawned[0][4]
        p.open(VIDEO, "u")
        self.assertNotEqual(first.rsplit("-", 1)[1], self.spawned[1][4].rsplit("-", 1)[1])

    def test_mpv_elsewhere(self):
        cmd = command("mpv", VIDEO, "u", {"cookies": "/c"}, "/s")
        self.assertEqual(cmd, ["mpv", "--fs", "--force-media-title=Кино",
                               "--input-ipc-server=/s", "u",
                               "--ytdl-raw-options=js-runtimes=quickjs,cookies=/c"])
        self.assertEqual(command("mpv", VIDEO, "u", {}, "/s")[-1],
                         "--ytdl-raw-options=js-runtimes=quickjs")

    def test_from_where_it_was_left(self):
        self.assertIn("start=754", command("/usr/bin/haltia-vplay", VIDEO, "u", {}, "/s",
                                           754.6))
        self.assertEqual(command("mpv", VIDEO, "u", {}, "/s", 754)[4], "--start=754")
        self.assertFalse(any("start" in c for c in command("mpv", VIDEO, "u", {}, "/s")[4:]))

    def test_no_ytdl_options_no_raw_list(self):
        cmd = command("/usr/bin/haltia-vplay", VIDEO, "u", {}, "/s")
        self.assertFalse(any(c.startswith("ytdl-raw-options") for c in cmd))

    def test_a_resolved_stream_plays_without_yt_dlp(self):
        p = self.player()
        asked = []
        p.open(VIDEO, "https://www.youtube.com/watch?v=x", {"cookies": "/c.txt"},
               resolve=lambda fmt: asked.append(fmt) or {"urls": ["https://v", "https://a"],
                                                         "user_agent": "UA 1"})
        self.assertEqual(self.spawned, [], "not before the stream is known")
        self.assertEqual(self.states[-1]["pos"], None)  # the bar says it loads
        self.pump(until=lambda: p.sock is not None)
        self.assertEqual(asked, [VPLAY_FORMAT])
        cmd = self.spawned[0]
        self.assertEqual(cmd[3], "edl://!no_clip;!no_chapters;%9%https://v;!new_stream;"
                                 "!no_clip;!no_chapters;%9%https://a")
        self.assertEqual(cmd[5:], ["ytdl=no", "user-agent=UA 1"])

    def test_a_failed_resolve_leaves_it_to_the_player(self):
        p = self.player()

        def fails(fmt):
            raise ValueError("no stream")

        p.open(VIDEO, "https://vkvideo.ru/video1_2", {}, resolve=fails)
        self.pump(until=lambda: p.sock is not None)
        self.assertEqual(self.spawned[0][3], "https://vkvideo.ru/video1_2")
        self.assertNotIn("ytdl=no", self.spawned[0])

    def test_a_stream_resolved_too_late_is_dropped(self):
        p = self.player()
        p.open(VIDEO, "u", resolve=lambda fmt: {"urls": ["https://old"]})
        p.stop()
        self.pump()
        self.assertEqual(self.spawned, [])
        self.assertIsNone(p.state)

    def test_mpv_resolves_itself(self):
        p = self.player()
        p.player = "/usr/bin/mpv"
        self.assertIsNone(p.stream_format)
        p.open(VIDEO, "u", resolve=lambda fmt: self.fail("asked"))
        self.assertEqual(self.spawned[0][-2:], ["u", "--ytdl-raw-options=js-runtimes=quickjs"])

    def test_mpv_uses_an_authenticated_stream_when_the_source_requires_it(self):
        p = self.player()
        p.player = "/usr/bin/mpv"
        asked = []
        p.open(VIDEO, "https://vkvideo.ru/video1_2", resolve_desktop=True,
               resolve=lambda fmt: asked.append(fmt) or {
                   "urls": ["https://cdn/private.mp4"], "user_agent": "Bound UA"})
        self.pump(until=lambda: p.sock is not None)
        self.assertEqual(asked, ["best"])
        self.assertIn("https://cdn/private.mp4", self.spawned[0])
        self.assertIn("--ytdl=no", self.spawned[0])
        self.assertIn("--user-agent=Bound UA", self.spawned[0])
        self.assertNotIn("https://vkvideo.ru/video1_2", self.spawned[0])

    def test_edl(self):
        self.assertEqual(edl(["https://both"]), "https://both")
        self.assertEqual(command("mpv", VIDEO, "u", {}, "/s", stream={"urls": ["https://b"]})[-2:],
                         ["https://b", "--ytdl=no"])
        self.assertTrue(edl(["https://в", "a"]).startswith("edl://!no_clip;!no_chapters;%10%"),
                        "lengths in bytes")


if __name__ == "__main__":
    unittest.main()
