# SPDX-License-Identifier: GPL-2.0
import unittest

from haltia_video.adapters.web.access import AccessPolicy, origin, proxy_settings


class AccessTest(unittest.TestCase):
    def test_external_url_is_canonical_and_rejects_paths_and_credentials(self):
        self.assertEqual(origin(" HTTPS://VIDEO.EXAMPLE:443/ "), "https://video.example")
        self.assertEqual(origin("http://[::1]:8765/"), "http://[::1]:8765")
        for url in ("ftp://video.example", "https://user:pass@video.example",
                    "https://video.example/video", "https://video.example?", "https://video.example#",
                    "https://video.example:", "https://video.example:0", "https://video.example:99999",
                    "https://video.example\\@other.example", "https://video.example\n.bad"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                origin(url)

    def test_trust_only_explicit_peer_ips(self):
        settings = proxy_settings(True, "https://video.example", "127.0.0.1, ::1, 127.0.0.1")
        self.assertEqual(settings["peers"], ["127.0.0.1", "::1"])
        for peers in ("localhost", "192.168.1.0/24", "*", ""):
            with self.subTest(peers=peers), self.assertRaises(ValueError):
                proxy_settings(True, "https://video.example", peers)

    def test_default_policy_remains_lan_only(self):
        policy = AccessPolicy()
        self.assertEqual(policy.request_origin("127.0.0.1", "http", "localhost:8765"),
                         "http://localhost:8765")
        self.assertEqual(policy.request_origin("::1", "http", "[::1]:8765"), "http://[::1]:8765")
        for peer, host in (("127.0.0.1", "video.example"), ("8.8.8.8", "127.0.0.1:8765")):
            with self.assertRaises(PermissionError):
                policy.request_origin(peer, "http", host)

    def test_proxy_uses_configured_origin_only_for_matching_host_and_socket(self):
        policy = AccessPolicy(proxy_settings(True, "https://video.example", "192.168.1.2, 8.8.8.8"))
        self.assertEqual(policy.request_origin("192.168.1.2", "http", "video.example"),
                         "https://video.example")
        self.assertEqual(policy.request_origin("8.8.8.8", "http", "video.example:443"),
                         "https://video.example")
        for peer, host in (("192.168.1.3", "video.example"), ("192.168.1.2", "attacker.example"),
                           ("8.8.8.8", "127.0.0.1"), ("192.168.1.2", "video.example:444")):
            with self.subTest(peer=peer, host=host), self.assertRaises(PermissionError):
                policy.request_origin(peer, "http", host)
        self.assertEqual(policy.request_origin("192.168.1.3", "http", "192.168.1.4:8765"),
                         "http://192.168.1.4:8765")
