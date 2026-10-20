# SPDX-License-Identifier: GPL-2.0
"""The Accounts' secrets in haltia-kit's Vault: where each source keeps
what, and a keyring the person will not unlock."""
import json
import os
import tempfile
import unittest

from haltia_kit.testing import FakeKeyring
from haltia_kit.vault import Locked

from sources import vault_in, vkvideo, youtube

VK_ACCOUNT = json.dumps({"token": "T", "id": 1, "name": "Анна"})


class AccountsTest(unittest.TestCase):
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
        v = vault_in(self.config, keyring=keyring, runtime_dir=self.runtime)
        self.addCleanup(v.close)
        return v

    def vk(self, ring):
        return vkvideo.VKVideo(http=None, config_dir=self.config, cache_dir=self.home,
                               vault=self.vault(ring))

    def yt(self, ring=None, sandboxed=False):
        return youtube.YouTube(http=None, config_dir=self.config, vault=self.vault(ring),
                               sandboxed=sandboxed)

    def test_kept_under_videos_id(self):
        self.assertEqual(self.vault(None).app, "io.github.haltia_de.Video")

    def test_vk_moves_its_token_into_the_keyring(self):
        ring = FakeKeyring()
        self.file("video-vk.json", VK_ACCOUNT)
        vk = self.vk(ring)
        self.assertEqual(vk.account(), {"name": "Анна"})
        self.assertFalse(os.path.exists(self.file("video-vk.json")))
        vk.logout()
        self.assertEqual(ring.kept, {})

    def test_vk_with_the_keyring_locked_is_a_guest_until_unlocked(self):
        ring = FakeKeyring(locked=True)
        ring.kept["video-vk.json"] = VK_ACCOUNT
        vk = self.vk(ring)
        self.assertTrue(vk.locked)
        self.assertIsNone(vk.account())
        vk.unlock()  # refused again
        self.assertTrue(vk.locked)
        ring.locked = False
        vk.unlock()
        self.assertFalse(vk.locked)
        self.assertEqual(vk.account(), {"name": "Анна"})
        self.assertIn("video-vk.json", ring.kept, "nothing dropped meanwhile")

    def test_youtube_signed_in_through_a_browser(self):
        self.file("video-youtube.json", json.dumps({"name": "аккаунт Google (Firefox)",
                                                    "browser": "firefox"}))
        yt = self.yt(FakeKeyring(locked=True))  # not needed for a browser's
        self.assertEqual(yt.account()["name"], "аккаунт Google (Firefox)")
        self.assertEqual(yt.ytdl_options(), {"cookies-from-browser": "firefox"})
        self.assertFalse(yt.locked)
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

    def test_youtube_with_the_keyring_locked_asks_once(self):
        ring = FakeKeyring(locked=True)
        ring.kept["youtube-cookies.txt"] = "# Netscape HTTP Cookie File\n"
        self.file("video-youtube.json", json.dumps({"name": "аккаунт Google"}))
        yt = self.yt(ring)
        asked = []
        lookup = ring.lookup
        ring.lookup = lambda name: (asked.append(name), lookup(name))[1]
        self.assertIsNone(yt.account())
        self.assertEqual(yt.ytdl_options(), {}, "plays as a guest")
        self.assertIsNone(yt.account())
        self.assertEqual(len(asked), 1, "not asked again at every call")
        ring.locked = False
        yt.unlock()
        self.assertEqual(yt.account()["name"], "аккаунт Google")
        self.assertIn("cookies", yt.ytdl_options())

    def test_youtube_checking_the_kept_file_while_locked(self):
        yt = self.yt(FakeKeyring(locked=True))
        with self.assertRaises(Locked):
            yt.login_check()  # as_file of what is kept: refused
        self.assertTrue(yt.locked)

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
