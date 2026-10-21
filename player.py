# SPDX-License-Identifier: GPL-2.0
"""The player: haltia-vplay (mpv elsewhere) as a process of its own, and what
it is playing, for the bar at the bottom of Видео.

    player = Player(post=...)          post(fn, *args) runs fn on the GTK side
    player.open(video, url, ytdl, start, resolve)
                                       play a Video (one at a time), from
                                       start seconds if given; resolve(fmt),
                                       when given, is asked for the stream
                                       first (for haltia-vplay: the source's
                                       stream()), off the GTK thread
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
import sys
import tempfile
import threading
import time

# The Haltia shell's (b8k-focus before its 2.0); elsewhere show() does nothing.
FOCUS = shutil.which("haltia-focus") or shutil.which("b8k-focus") or ""
CONNECT_SEC = 15  # mpv opens its socket at start, before yt-dlp is asked
OBSERVED = ("time-pos", "duration", "pause")


VPLAY = ("haltia-vplay", "b8k-vplay")  # b8k-vplay: its name before 2.0, until 3.0
# What haltia-vplay plays (its own ytdl-format): H.264 at most 480p (VP9
# and AV1 cost more) and the AAC audio. Asked of a source's stream(), so
# that the tablet starts no yt-dlp of its own for every video.
VPLAY_FORMAT = "bv*[height<=480][vcodec^=avc1]+ba[ext=m4a]/b[height<=480]/b"


def find_player(which=shutil.which):
    """haltia-vplay where it is installed (the tablet's package), else mpv
    (a desktop's deb and Flatpak bring mpv, not vplay), else None. Looked
    up on PATH, wherever the package put it."""
    for name in VPLAY + ("mpv",):
        path = which(name)
        if path:
            return path
    return None


def edl(urls):
    """One URL for mpv out of a video and an audio stream: mpv's EDL, as its
    yt-dlp hook builds it."""
    if len(urls) == 1:
        return urls[0]
    return "edl://" + ";!new_stream;".join(
        "!no_clip;!no_chapters;%%%d%%%s" % (len(u.encode()), u) for u in urls)


def command(player, video, url, ytdl, ipc, start=None, stream=None):
    """The command line that plays url in player (haltia-vplay, else mpv),
    from start seconds when given; a stream (a source's stream()) is
    played as it is, without yt-dlp."""
    raw = dict(ytdl or {})
    opts = []
    if stream:
        url, raw = edl(stream["urls"]), {}
        opts = ["ytdl=no"] + (["user-agent=" + stream["user_agent"]]
                              if stream.get("user_agent") else [])
    if os.path.basename(player) in VPLAY:
        cmd = [player, "--title", video["title"], url, "input-ipc-server=" + ipc] + opts
        if start:
            cmd.append("start=%d" % start)
        if raw:
            # Replaces haltia-vplay's own list, so its runtime goes too.
            raw = dict({"js-runtimes": "quickjs"}, **raw)
            cmd.append("ytdl-raw-options=" + ",".join("%s=%s" % kv for kv in raw.items()))
    else:
        cmd = [player, "--fs", "--force-media-title=" + video["title"],
               "--input-ipc-server=" + ipc] + (["--start=%d" % start] if start else []) + [url]
        cmd += ["--" + o for o in opts]
        if not stream:
            # yt-dlp's own default is deno alone; QuickJS is what comes with
            # Видео (the deb and Flatpak bring it, as Alpine does).
            raw = dict({"js-runtimes": "quickjs"}, **raw)
            cmd.append("--ytdl-raw-options=" + ",".join("%s=%s" % kv for kv in raw.items()))
    return cmd


def connect_unix(path, give_up, alive=lambda: True):
    """mpv's socket once it is there (or None by give_up, a time, or once
    alive() says the player has exited)."""
    while time.time() < give_up and alive():
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


def terminate(proc):
    """End a player that cannot be told to quit (proc: spawn's answer)."""
    if proc is not None and getattr(proc, "returncode", 0) is None:
        try:
            proc.terminate()
        except OSError:
            pass


class Player:
    def __init__(self, post, spawn=popen, connect=connect_unix, runtime_dir=None,
                 player=None, focus=FOCUS):
        self.post = post
        self.spawn = spawn
        self.connect = connect
        self.runtime_dir = runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
        self.sock_dir = None  # made at the first open
        self.player = player or find_player()
        self.focus = focus
        self.state = None
        self.watchers = []
        self.sock = None
        self.proc = None  # the current run's process (spawn's answer)
        self.run_no = 0  # each open() is a run; older runs' events are dropped

    def watch(self, fn):
        self.watchers.append(fn)

    def _tell(self):
        for fn in list(self.watchers):
            fn(self.state)

    # --- starting and stopping ------------------------------------------------
    @property
    def stream_format(self):
        """The format a source's stream() is asked for, or None when the
        player asks yt-dlp itself (mpv: a desktop's CPU does not mind)."""
        return VPLAY_FORMAT if os.path.basename(self.player or "") in VPLAY else None

    def open(self, video, url, ytdl=None, start=None, resolve=None):
        """Play video (the one playing is closed). OSError: no player."""
        if not self.player:
            raise OSError("neither haltia-vplay nor mpv is installed")
        self.stop()
        self.run_no += 1
        self.state = {"video": video, "pos": None, "duration": None, "paused": False,
                      "start": start}
        self._tell()
        args = (self.run_no, video, url, ytdl, start)
        fmt = self.stream_format
        if not (resolve and fmt):
            self._start(*args, None)
            return

        def resolving():
            try:
                stream = resolve(fmt)
            except Exception as exc:  # the player's own yt-dlp will try
                sys.stderr.write("haltia-video: %s\n" % exc)
                stream = None
            self.post(self._start, *args, stream)

        threading.Thread(target=resolving, daemon=True).start()

    def _start(self, run_no, video, url, ytdl, start, stream):
        if run_no != self.run_no or self.state is None:
            return  # stopped, or another video opened, while it was resolved
        if self.sock_dir is None:
            self.sock_dir = private_dir(self.runtime_dir)
        ipc = os.path.join(self.sock_dir, "mpv-%d-%d-%s.sock"
                           % (os.getpid(), run_no, secrets.token_hex(4)))
        try:
            os.unlink(ipc)
        except OSError:
            pass
        self.proc = self.spawn(command(self.player, video, url, ytdl, ipc, start, stream))
        threading.Thread(target=self._read, args=(run_no, ipc, self.proc), daemon=True).start()

    def stop(self):
        if self.state is not None:
            # Not connected yet (still starting): no quit reaches it, and a
            # second libmpv on the tablet's 1 GB must not play on unseen.
            if not self._send("quit"):
                terminate(self.proc)
            self._ended(self.run_no)

    # --- the reader thread ----------------------------------------------------
    def _read(self, run_no, ipc, proc):
        # returncode rather than poll(): popen's waiter thread holds the lock
        # poll() needs, and sets returncode once the player has exited.
        sock = self.connect(ipc, time.time() + CONNECT_SEC,
                            lambda: getattr(proc, "returncode", None) is None)
        if sock is None:
            terminate(proc)  # given up on: no bar, so no player either
            self.post(self._ended, run_no)
            return
        self.post(self._connected, run_no, sock)
        try:
            for n, name in enumerate(OBSERVED, 1):
                sock.sendall((json.dumps({"command": ["observe_property", n, name]})
                              + "\n").encode())
        except OSError:
            pass  # closed already (a run stopped meanwhile): recv ends it
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
            try:  # stopped meanwhile: it must not play on unseen
                sock.sendall(b'{"command": ["quit"]}\n')
            except OSError:
                pass
            sock.close()
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
        self.proc = None
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
