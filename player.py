# SPDX-License-Identifier: GPL-2.0
"""The player: haltia-vplay (mpv elsewhere) as a process of its own, and what
it is playing, for the bar at the bottom of Видео.

    player = Player(post=...)          post(fn, *args) runs fn on the GTK side
    player.open(video, url, ytdl, start)  play a Video (one at a time),
                                       from start seconds if given
    player.state                       None, or {"video", "pos", "duration",
                                       "paused", "start"} (pos and duration
                                       in seconds or None until mpv tells them)
    player.watch(fn)                   fn(state) when it changes (pos: to the
                                       second, not every frame)
    player.play() / toggle() / seek(sec) / stop() / show()

The player is started with mpv's JSON IPC (input-ipc-server, which
haltia-vplay passes on to libmpv), on a socket with an unguessable name in a
directory only this user can reach, and a thread of this module reads it;
spawn, connect and focus can be replaced (the tests talk through a
socketpair). When the window is closed or the video ends, the socket
closes and the state goes back to None. Not thread-safe apart from that
reader: every call comes from the GTK thread.
"""
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time

# The Haltia shell's (b8k-focus before its 2.0); elsewhere show() does nothing.
FOCUS = shutil.which("haltia-focus") or shutil.which("b8k-focus") or ""
CONNECT_SEC = 15  # mpv opens its socket at start, before yt-dlp is asked
OBSERVED = ("time-pos", "duration", "pause")


def command(player, video, url, ytdl, ipc, start=None):
    """The command line that plays url in player (haltia-vplay, else mpv),
    from start seconds when given."""
    raw = dict(ytdl or {})
    if os.path.basename(player) in ("haltia-vplay", "b8k-vplay"):
        cmd = [player, "--title", video["title"], url, "input-ipc-server=" + ipc]
        if start:
            cmd.append("start=%d" % start)
        if raw:
            # Replaces haltia-vplay's own list, so its runtime goes too.
            raw = dict({"js-runtimes": "quickjs"}, **raw)
            cmd.append("ytdl-raw-options=" + ",".join("%s=%s" % kv for kv in raw.items()))
    else:
        cmd = [player, "--fs", "--force-media-title=" + video["title"],
               "--input-ipc-server=" + ipc] + (["--start=%d" % start] if start else []) + [url]
        if raw:
            cmd.append("--ytdl-raw-options=" + ",".join("%s=%s" % kv for kv in raw.items()))
    return cmd


def connect_unix(path, give_up):
    """mpv's socket once it is there (or None by give_up, a time)."""
    while time.time() < give_up:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
            return sock
        except OSError:
            sock.close()
            time.sleep(0.2)
    return None


def private_dir(runtime_dir):
    """A directory for the sockets that only this user can reach: haltia-video
    in the runtime directory if it is ours and closed to others, else a new
    one (someone else's directory, or a link to it, would let them answer
    in mpv's place)."""
    if runtime_dir:
        path = os.path.join(runtime_dir, "haltia-video")
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError:
            path = None
        if path:
            st = os.lstat(path)
            if (stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()
                    and not st.st_mode & 0o077):
                return path
    return tempfile.mkdtemp(prefix="haltia-video-")


def popen(cmd):
    proc = subprocess.Popen(cmd, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Waited for when it exits: else every player closed stays a zombie.
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


class Player:
    def __init__(self, post, spawn=popen, connect=connect_unix, runtime_dir=None,
                 player=None, focus=FOCUS):
        self.post = post
        self.spawn = spawn
        self.connect = connect
        self.runtime_dir = runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
        self.sock_dir = None  # made at the first open
        # haltia-vplay where it is installed (PREFIX/bin: /usr/local, or /usr
        # from the package), else mpv.
        self.player = player or shutil.which("haltia-vplay") or shutil.which("b8k-vplay") or "mpv"
        self.focus = focus
        self.state = None
        self.watchers = []
        self.sock = None
        self.run_no = 0  # each open() is a run; older runs' events are dropped

    def watch(self, fn):
        self.watchers.append(fn)

    def _tell(self):
        for fn in list(self.watchers):
            fn(self.state)

    # --- starting and stopping ------------------------------------------------
    def open(self, video, url, ytdl=None, start=None):
        """Play video (the one playing is closed). OSError: no player."""
        self.stop()
        self.run_no += 1
        if self.sock_dir is None:
            self.sock_dir = private_dir(self.runtime_dir)
        ipc = os.path.join(self.sock_dir, "mpv-%d-%d-%s.sock"
                           % (os.getpid(), self.run_no, secrets.token_hex(4)))
        try:
            os.unlink(ipc)
        except OSError:
            pass
        self.spawn(command(self.player, video, url, ytdl, ipc, start))
        self.state = {"video": video, "pos": None, "duration": None, "paused": False,
                      "start": start}
        self._tell()
        threading.Thread(target=self._read, args=(self.run_no, ipc), daemon=True).start()

    def stop(self):
        if self.state is not None:
            self._send("quit")
            self._ended(self.run_no)

    # --- the reader thread ----------------------------------------------------
    def _read(self, run_no, ipc):
        sock = self.connect(ipc, time.time() + CONNECT_SEC)
        if sock is None:
            self.post(self._ended, run_no)
            return
        self.post(self._connected, run_no, sock)
        for n, name in enumerate(OBSERVED, 1):
            sock.sendall((json.dumps({"command": ["observe_property", n, name]})
                          + "\n").encode())
        buf = b""
        while True:
            try:
                data = sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("event") == "property-change":
                    self.post(self._changed, run_no, msg.get("name"), msg.get("data"))
        sock.close()
        try:
            os.unlink(ipc)
        except OSError:
            pass
        self.post(self._ended, run_no)

    def _connected(self, run_no, sock):
        if run_no != self.run_no or self.state is None:
            sock.close()  # stopped meanwhile
            return
        self.sock = sock

    def _changed(self, run_no, name, value):
        if run_no != self.run_no or self.state is None:
            return
        key = {"time-pos": "pos", "duration": "duration", "pause": "paused"}.get(name)
        if key is None:
            return
        old = self.state[key]
        if key == "paused":
            value = bool(value)
        same = (old == value if key != "pos" or None in (old, value)
                else int(old) == int(value))
        self.state[key] = value
        if not same:
            self._tell()

    def _ended(self, run_no):
        if run_no != self.run_no or self.state is None:
            return
        self.state = None
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        self._tell()

    # --- control ---------------------------------------------------------------
    def _send(self, *cmd):
        if self.sock is None:
            return False
        try:
            self.sock.sendall((json.dumps({"command": list(cmd)}) + "\n").encode())
            return True
        except OSError:
            return False

    def play(self):
        self._send("set_property", "pause", False)

    def toggle(self):
        if self.state:
            self._send("set_property", "pause", not self.state["paused"])

    def seek(self, sec):
        self._send("seek", float(sec), "absolute")

    def show(self):
        """Bring the player's window to the front (the shell's haltia-focus)."""
        if self.state and os.access(self.focus, os.X_OK):
            try:
                self.spawn([self.focus, "activate", "haltia-vplay"])
            except OSError:
                pass
