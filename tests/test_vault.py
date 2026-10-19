# SPDX-License-Identifier: GPL-2.0
import json
import os
import stat
import tempfile
import unittest

from sources import vkvideo, youtube
from sources.vault import Vault


class FakeKeyring:
    def __init__(self, answers=True):
        self.on = answers
        self.kept = {}

    def answers(self):
        return self.on

    def lookup(self, name):
        return self.kept.get(name)

    def store(self, name, text):
        self.kept[name] = text

    def clear(self, name):
        self.kept.pop(name, None)


class Dirs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.config = os.path.join(self.home, "config")
        self.runtime = os.path.join(self.home, "run")
        os.makedirs(self.config)
        os.makedirs(self.runtime, 0o700)

    def file(self, name, text=None):
        path = os.path.join(self.config, name)
        if text is not None:
            with open(path, "w") as fh:
                fh.write(text)
        return path

    def vault(self, keyring):
        v = Vault(self.config, keyring=keyring, runtime_dir=self.runtime)
        self.addCleanup(v.close)
        return v


class FilesTest(Dirs):
    """No keyring (the tablet): the files of before, readable by the owner only."""

    def test_kept_in_files(self):
        for keyring in (None, FakeKeyring(answers=False)):
            v = self.vault(keyring)
            v.put("a.json", "secret")
            self.assertEqual(v.kind, "file")
            self.assertEqual(v.get("a.json"), "secret")
            self.assertEqual(stat.S_IMODE(os.stat(self.file("a.json")).st_mode), 0o600)
            self.assertEqual(v.as_file("a.json"), self.file("a.json"))
            v.drop("a.json")
            self.assertIsNone(v.get("a.json"))
            self.assertIsNone(v.as_file("a.json"))

    def test_take_leaves_the_file_where_it_is(self):
        v = self.vault(None)
        self.assertFalse(v.take("c.txt"))
        self.file("c.txt", "cookies")
        self.assertTrue(v.take("c.txt"))
        self.assertEqual(v.get("c.txt"), "cookies")


class KeyringTest(Dirs):
    def test_kept_in_the_keyring_not_on_disk(self):
        ring = FakeKeyring()
        v = self.vault(ring)
        v.put("a.json", "secret")
        self.assertEqual(v.kind, "keyring")
        self.assertEqual(ring.kept, {"a.json": "secret"})
        self.assertFalse(os.path.exists(self.file("a.json")))
        self.assertEqual(v.get("a.json"), "secret")
        v.drop("a.json")
        self.assertEqual(ring.kept, {})
        self.assertIsNone(v.get("a.json"))

    def test_an_old_file_moves_in_once(self):
        ring = FakeKeyring()
        self.file("video-vk.json", '{"token": "T"}')
        v = self.vault(ring)
        self.assertEqual(v.get("video-vk.json"), '{"token": "T"}')
        self.assertEqual(ring.kept, {"video-vk.json": '{"token": "T"}'})
        self.assertFalse(os.path.exists(self.file("video-vk.json")))
        self.assertEqual(self.vault(ring).get("video-vk.json"), '{"token": "T"}')

    def test_a_new_file_replaces_what_was_kept(self):
        ring = FakeKeyring()
        ring.kept["c.txt"] = "old"
        v = self.vault(ring)
        self.assertEqual(v.get("c.txt"), "old")
        self.file("c.txt", "new")
        self.assertTrue(v.take("c.txt"))
        self.assertEqual((v.get("c.txt"), ring.kept["c.txt"]), ("new", "new"))
        self.assertFalse(os.path.exists(self.file("c.txt")))

    def test_a_copy_for_yt_dlp_goes_back_changed_and_away(self):
        ring = FakeKeyring()
        ring.kept["c.txt"] = "cookies"
        v = self.vault(ring)
        path = v.as_file("c.txt")
        self.assertTrue(path.startswith(self.runtime))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(v.as_file("c.txt"), path, "one copy per run")
        with open(path, "w") as fh:
            fh.write("cookies, renewed")
        v.close()
        self.assertEqual(ring.kept["c.txt"], "cookies, renewed")
        self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_a_dropped_secret_leaves_no_copy(self):
        ring = FakeKeyring()
        ring.kept["c.txt"] = "cookies"
        v = self.vault(ring)
        path = v.as_file("c.txt")
        v.drop("c.txt")
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(v.as_file("c.txt"))


class AccountsTest(Dirs):
    def test_vk_moves_its_token_into_the_keyring(self):
        ring = FakeKeyring()
        self.file("video-vk.json", json.dumps({"token": "T", "id": 1, "name": "Анна"}))
        vk = vkvideo.VKVideo(http=None, config_dir=self.config, cache_dir=self.home,
                             vault=self.vault(ring))
        self.assertEqual(vk.account(), {"name": "Анна"})
        self.assertFalse(os.path.exists(self.file("video-vk.json")))
        vk.logout()
        self.assertEqual(ring.kept, {})

    def yt(self, ring=None, sandboxed=False):
        return youtube.YouTube(http=None, config_dir=self.config, vault=self.vault(ring),
                               sandboxed=sandboxed)

    def test_youtube_signed_in_through_a_browser(self):
        self.file("video-youtube.json", json.dumps({"name": "аккаунт Google (Firefox)",
                                                    "browser": "firefox"}))
        yt = self.yt(FakeKeyring())
        self.assertEqual(yt.account()["name"], "аккаунт Google (Firefox)")
        self.assertEqual(yt.ytdl_options(), {"cookies-from-browser": "firefox"})
        yt.logout()
        self.assertIsNone(yt.account())

    def test_youtube_cookies_from_the_keyring_to_the_player(self):
        ring = FakeKeyring()
        ring.kept["youtube-cookies.txt"] = "# Netscape HTTP Cookie File\n"
        self.file("video-youtube.json", json.dumps({"name": "аккаунт Google"}))
        yt = self.yt(ring)
        path = yt.ytdl_options()["cookies"]
        self.assertTrue(path.startswith(self.runtime))
        yt.logout()
        self.assertIsNone(yt.account())
        self.assertEqual(ring.kept, {})

    def test_which_browsers(self):
        def exists(*present):
            return lambda path: any(path.endswith(p) for p in present)
        self.assertEqual(youtube.browsers(exists(".mozilla/firefox", ".config/chromium"), False),
                         [("firefox", "Firefox"), ("chromium", "Chromium")])
        self.assertEqual(youtube.browsers(exists("snap/chromium/common/chromium"), False),
                         [("chromium", "Chromium")])
        self.assertEqual(youtube.browsers(exists(".mozilla/firefox"), True), [],
                         "Flatpak's sandbox hides the profiles")
        self.assertEqual(youtube.browsers(exists(".mozilla/firefox", "/.flatpak-info")), [])
        self.assertEqual(self.yt(sandboxed=True).browsers(), [])


if __name__ == "__main__":
    unittest.main()
