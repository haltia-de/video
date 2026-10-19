# SPDX-License-Identifier: GPL-2.0
"""An Account's secrets (the VK token, the YouTube cookies), kept in the
Secret Service through libsecret: the desktop's keyring, or the Secret
portal in Flatpak (libsecret picks it there by itself). Where none
answers (the tablet has no keyring) they stay files in the config folder,
readable by the owner only, as before 2.1.

    vault = Vault(config_dir)
    vault.kind             "keyring" or "file" (asked at the first use)
    vault.get(name)        the text kept under name, or None
    vault.put(name, text)
    vault.take(name)       the file of that name the person has just put
                           in place goes into the keyring (a new cookies
                           file); False when there is none
    vault.drop(name)
    vault.as_file(name)    a path holding the text, for yt-dlp and mpv,
                           or None

name is the file's name ("video-vk.json"): the same file is the fallback,
and a file of 2.0 or before found while the keyring answers moves into it
once. as_file() with the keyring writes a copy in a private folder of
the runtime directory; yt-dlp updates the cookies in it, so at exit a
changed copy goes back into the keyring before the folder is removed.
Thread-safe: the sources call it from worker threads.
"""
import atexit
import os
import shutil
import tempfile
import threading

AUTO = "auto"
SCHEMA = "io.github.haltia_de.Video"


class Keyring:
    """The Secret Service through libsecret (gi), one string per name."""

    def __init__(self):
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import GLib, Secret

        self.Secret = Secret
        self.Error = GLib.Error
        self.schema = Secret.Schema.new(SCHEMA, Secret.SchemaFlags.NONE,
                                        {"name": Secret.SchemaAttributeType.STRING})

    def answers(self):
        try:
            self.lookup("-")
            return True
        except self.Error:  # no Secret Service on this bus
            return False

    def lookup(self, name):
        return self.Secret.password_lookup_sync(self.schema, {"name": name}, None)

    def store(self, name, text):
        self.Secret.password_store_sync(self.schema, {"name": name},
                                        self.Secret.COLLECTION_DEFAULT,
                                        "Видео: %s" % name, text, None)

    def clear(self, name):
        self.Secret.password_clear_sync(self.schema, {"name": name}, None)


def _keyring():
    try:
        return Keyring()
    except (ImportError, ValueError):  # no libsecret (its typelib)
        return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _write(path, text):
    """Readable by the owner only, written whole or not at all."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


class Vault:
    def __init__(self, config_dir, keyring=AUTO, runtime_dir=None):
        """keyring: AUTO (libsecret, if it answers), None (files only, as
        in the tests), or anything with answers/lookup/store/clear."""
        self.dir = config_dir
        self.keyring = _keyring() if keyring == AUTO else keyring
        self.kind = None if self.keyring else "file"
        self.runtime_dir = runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
        self.lock = threading.Lock()
        self.kept = {}  # name: text (or None) as the keyring gave it
        self.copies = {}  # name: (path, text) written by as_file
        self.copy_dir = None

    def _path(self, name):
        return os.path.join(self.dir, name)

    def _on_keyring(self):
        if self.kind is None:
            self.kind = "keyring" if self.keyring.answers() else "file"
        return self.kind == "keyring"

    def _get(self, name):
        if not self._on_keyring():
            return _read(self._path(name))
        if name not in self.kept:
            try:
                text = self.keyring.lookup(name)
            except Exception:  # locked and not unlocked, gone meanwhile: not signed in
                return None
            if text is None:
                text = _read(self._path(name))
                if text is not None:  # 2.0's file, moved in once
                    self.keyring.store(name, text)
                    _remove(self._path(name))
            self.kept[name] = text
        return self.kept[name]

    def get(self, name):
        with self.lock:
            return self._get(name)

    def put(self, name, text):
        with self.lock:
            if self._on_keyring():
                self.keyring.store(name, text)
                self.kept[name] = text
                self._forget_copy(name)
            else:
                _write(self._path(name), text)

    def take(self, name):
        with self.lock:
            text = _read(self._path(name))
            if text is None:
                return False
            if self._on_keyring():
                self.keyring.store(name, text)
                self.kept[name] = text
                self._forget_copy(name)
                _remove(self._path(name))
            return True

    def drop(self, name):
        with self.lock:
            if self._on_keyring():
                try:
                    self.keyring.clear(name)
                except Exception:
                    pass
                self.kept[name] = None
                self._forget_copy(name)
            _remove(self._path(name))

    def as_file(self, name):
        with self.lock:
            if not self._on_keyring():
                path = self._path(name)
                return path if os.path.exists(path) else None
            text = self._get(name)
            if text is None:
                return None
            if name not in self.copies:
                if self.copy_dir is None:
                    # mkdtemp: a new folder only this user can enter.
                    self.copy_dir = tempfile.mkdtemp(prefix="haltia-video-",
                                                     dir=self.runtime_dir or None)
                    atexit.register(self.close)
                path = os.path.join(self.copy_dir, name)
                _write(path, text)
                self.copies[name] = (path, text)
            return self.copies[name][0]

    def _forget_copy(self, name):
        path, _text = self.copies.pop(name, (None, None))
        if path:
            _remove(path)

    def close(self):
        """What yt-dlp changed in the copies back into the keyring; the
        copies removed."""
        with self.lock:
            for name, (path, text) in list(self.copies.items()):
                now = _read(path)
                if now and now != text:
                    try:
                        self.keyring.store(name, now)
                        self.kept[name] = now
                    except Exception:
                        pass
            self.copies = {}
            if self.copy_dir:
                shutil.rmtree(self.copy_dir, ignore_errors=True)
                self.copy_dir = None
