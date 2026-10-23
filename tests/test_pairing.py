# SPDX-License-Identifier: GPL-2.0
import json
import os
import tempfile
import unittest

from haltia_video.adapters.web.pairing import Pairing


class PairingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "web.json")
        self.now = 100
        self.pairing = Pairing(self.path, clock=lambda: self.now)

    def test_one_time_grant_is_hashed_and_survives_restart_until_revoked(self):
        pending = self.pairing.issue()
        device, token = self.pairing.pair(pending["secret"], "Tablet")
        self.assertEqual(self.pairing.authenticate(token), device)
        with open(self.path) as fh:
            data = json.load(fh)
        self.assertNotIn(token, json.dumps(data))
        self.assertNotIn(pending["code"], json.dumps(data))
        again = Pairing(self.path)
        self.assertEqual(again.authenticate(token), device)
        with self.assertRaises(ValueError):
            self.pairing.pair(pending["secret"], "Replay")
        again.revoke(device)
        self.assertIsNone(again.authenticate(token))

    def test_expiry_and_attempt_limit(self):
        pending = self.pairing.issue()
        for _ in range(5):
            with self.assertRaises(ValueError):
                self.pairing.pair("bad", "Browser")
        with self.assertRaises(ValueError):
            self.pairing.pair(pending["code"], "Browser")
        pending = self.pairing.issue()
        self.now += 301
        with self.assertRaises(ValueError):
            self.pairing.pair(pending["code"], "Browser")
