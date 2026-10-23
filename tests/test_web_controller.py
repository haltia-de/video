# SPDX-License-Identifier: GPL-2.0
import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from haltia_video.adapters.web.controller import Server


class ControllerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.posts = []
        self.core = SimpleNamespace(runtime=SimpleNamespace(post=self.posts.append), closed=False,
                                    sessions=SimpleNamespace(sessions={}), revision=0)
        self.server = Server(self.core, self.tmp.name, lambda: None)

    async def test_stop_before_ready_still_closes_listener(self):
        closed = []
        started = asyncio.Event()

        class FakeApplication:
            def __init__(self, *args):
                self.stopped = asyncio.Event()

            async def start(self, port):
                started.set()

            async def close(self):
                closed.append(True)

            def publish(self, event):
                pass

        self.server.state = "starting"
        task = asyncio.create_task(self.server._serve(FakeApplication, 0, ["127.0.0.1"]))
        await started.wait()
        self.server.stop()
        self.posts.pop(0)()  # The queued ready notification arrives after stop.
        await task
        self.posts.pop(0)()
        self.assertEqual(closed, [True])
        self.assertEqual(self.server.state, "off")
        self.assertIsNone(self.server.loop)

    async def test_failed_start_closes_partially_created_resources(self):
        closed = []

        class FakeApplication:
            def __init__(self, *args):
                pass

            async def start(self, port):
                raise OSError("port busy")

            async def close(self):
                closed.append(True)

        with self.assertRaises(OSError):
            await self.server._serve(FakeApplication, 0, [])
        self.assertEqual(closed, [True])
        self.assertEqual(self.posts, [])

    async def test_shutdown_before_async_start_needs_no_running_owner_loop(self):
        closed = []

        class FakeApplication:
            def __init__(self, *args):
                self.stopped = asyncio.Event()

            async def start(self, port):
                pass

            async def close(self):
                closed.append(True)

        self.server.state = "starting"
        self.server.stop()
        await asyncio.wait_for(self.server._serve(FakeApplication, 0, []), 1)
        self.assertEqual(closed, [True])
        self.assertEqual(len(self.posts), 1)  # ended only; no ready callback is needed

    async def test_enable_while_stopping_restarts_only_when_requested(self):
        self.server.state = "stopping"
        self.server.enable(True)
        self.assertTrue(self.server.restart_requested)
        self.server.start = Mock()
        self.server._ended(0)
        self.server.start.assert_called_once()
        self.server.start.reset_mock()
        self.server.stop()
        self.server._ended(0)
        self.server.start.assert_not_called()

    async def test_proxy_configuration_persists_and_precedes_lan_pairing_links(self):
        self.server.configure_proxy(True, "https://video.example/", "127.0.0.1, ::1")
        again = Server(self.core, self.tmp.name, lambda: None)
        self.assertTrue(again.snapshot()["proxy"]["enabled"])
        self.assertEqual(again.snapshot()["urls"][0], "https://video.example/")
        before = self.server.snapshot()["proxy"]
        with self.assertRaises(ValueError):
            self.server.configure_proxy(True, "https://video.example/path", "*")
        self.assertEqual(self.server.snapshot()["proxy"], before)
        self.server.state = "on"
        with self.assertRaises(ValueError):
            self.server.configure_proxy(False, "", "")
