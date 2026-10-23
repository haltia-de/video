# SPDX-License-Identifier: GPL-2.0
import asyncio
from concurrent.futures import Future
import importlib.util
import tempfile
from types import SimpleNamespace
import unittest

from library import Library
from haltia_video.core.application import Application
from haltia_video.adapters.web.pairing import Pairing
from haltia_video.adapters.web.access import AccessPolicy, proxy_settings

VIDEO = {"source": "vk", "id": "-7_1", "title": "Video", "duration": 600}


@unittest.skipUnless(importlib.util.find_spec("aiohttp"), "optional aiohttp is not installed")
class WebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import aiohttp
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from haltia_video.adapters.web.http import WebApplication
        self.tmp = tempfile.TemporaryDirectory()
        self.pairing = Pairing(self.tmp.name + "/web.json")
        self.requests = []

        async def upstream(request):
            self.requests.append(dict(request.headers))
            if request.headers.get("User-Agent") != "Bound-UA":
                return web.Response(status=400)
            data = b"0123456789abcdef"
            if request.headers.get("Range") == "bytes=4-7":
                return web.Response(body=data[4:8], status=206,
                                    headers={"Content-Range": "bytes 4-7/16", "Accept-Ranges": "bytes"})
            return web.Response(body=data, content_type="video/mp4", headers={"Accept-Ranges": "bytes"})
        upstream_app = web.Application()
        upstream_app.router.add_get("/video", upstream)
        self.upstream = TestServer(upstream_app)
        await self.upstream.start_server()
        self.source = SimpleNamespace(id="vk", label="VK", watch_url=lambda v: "https://vkvideo.ru/video" + v["id"],
                                      browser_stream=lambda v, f: {"tracks": [{
                                          "url": str(self.upstream.make_url("/video")), "container": "mp4",
                                          "kind": "combined", "headers": {"User-Agent": "Bound-UA"}}],
                                          "duration": 600, "height": 480})
        loop = asyncio.get_running_loop()
        def dispatch(fn, *args):
            future = Future()
            try:
                future.set_result(fn(*args))
            except Exception as exc:
                future.set_exception(exc)
            return future
        def work(fn, *args, done):
            def invoke():
                try:
                    result, error = fn(*args), None
                except Exception as exc:
                    result, error = None, exc
                done(result, error)
            loop.call_soon(invoke)
        runtime = SimpleNamespace(dispatch=dispatch, work=work, later=lambda *a: None)
        self.core = Application({"vk": self.source}, Library(self.tmp.name, self.tmp.name), runtime)
        self.controller = SimpleNamespace(pairing=self.pairing, internal_key="INTERNAL", port=0, changed=lambda: None)
        self.http = WebApplication(self.controller, self.core)
        self.core.watch(self.http.publish)
        await self.http.media.start()
        self.client = TestClient(TestServer(self.http.app), cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        self.controller.port = self.client.server.port
        self.client.session.headers["Origin"] = str(self.client.make_url("/")).rstrip("/")

    async def asyncTearDown(self):
        await self.http.close()
        await self.client.close()
        await self.upstream.close()
        self.core.close()
        self.tmp.cleanup()

    async def pair(self):
        code = self.pairing.issue()["code"]
        response = await self.client.post("/pair", json={"code": code, "name": "Tablet"})
        self.assertEqual(response.status, 200)
        return (await response.json())["device"]

    async def start_playback(self, nonce="one"):
        response = await self.client.post("/api/play", json={"video": VIDEO, "request": nonce})
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def test_private_api_requires_pairing_and_rejects_other_origins(self):
        response = await self.client.post("/api/query", json={"view": "state"})
        self.assertEqual(response.status, 401)
        await self.pair()
        response = await self.client.post("/api/query", json={"view": "state"})
        self.assertEqual(response.status, 200)
        self.assertNotIn("hash", await response.text())
        response = await self.client.post("/api/command", json={"name": "shorts", "params": {"on": True}},
                                          headers={"Origin": "http://attacker.example"})
        self.assertEqual(response.status, 403)
        self.assertFalse(self.core.library.shorts)
        response = await self.client.get("/", headers={"Host": "attacker.example"})
        self.assertEqual(response.status, 403)

    async def test_https_proxy_pairing_commands_events_and_media(self):
        self.http.access = AccessPolicy(proxy_settings(True, "https://video.example", "127.0.0.1"))
        self.client.session.headers.update({"Host": "video.example", "Origin": "https://video.example"})
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        await response.read()
        response = await self.client.post("/api/query", json={"view": "state"})
        self.assertEqual(response.status, 401)
        response = await self.client.post("/pair", json={"code": self.pairing.issue()["code"], "name": "Proxy"})
        self.assertEqual(response.status, 200)
        cookie = response.cookies["haltia_device"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        # The test connection is plain HTTP behind TLS termination. A browser
        # would send this Secure cookie to nginx over the public HTTPS URL.
        self.client.session.headers["Cookie"] = "haltia_device=" + cookie.value
        response = await self.client.post("/api/query", json={"view": "state"})
        self.assertEqual(response.status, 200)
        response = await self.client.post("/api/command", json={"name": "shorts", "params": {"on": True}})
        self.assertEqual(response.status, 200)
        self.assertTrue(self.core.library.shorts)
        stream = await self.client.get("/events")
        self.assertEqual(stream.status, 200)
        self.assertIn(b"resync", await stream.content.readline())
        stream.close()
        session = await self.start_playback()
        response = await self.client.get(session["url"], headers={"Range": "bytes=4-7"})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), b"4567")
        self.assertNotIn("Cookie", self.requests[-1])
        response = await self.client.post("/api/progress", json={"id": session["id"], "generation": 0,
                                                               "pos": 120, "duration": 600, "paused": True})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.core.library.resume_at(VIDEO), 120)
        response = await self.client.get("/track/any/0?key=INTERNAL")
        self.assertEqual(response.status, 403)

    async def test_proxy_rejects_untrusted_peer_host_and_origin_even_with_forwarded_headers(self):
        settings = proxy_settings(True, "https://video.example", "127.0.0.1")
        self.http.access = AccessPolicy(settings)
        headers = {"Host": "video.example", "Origin": "https://attacker.example",
                   "X-Forwarded-Host": "video.example", "X-Forwarded-Proto": "https",
                   "X-Forwarded-For": "127.0.0.1"}
        response = await self.client.post("/pair", json={}, headers=headers)
        self.assertEqual(response.status, 403)
        headers["Origin"] = "https://video.example"
        headers["Host"] = "attacker.example"
        response = await self.client.get("/", headers=headers)
        self.assertEqual(response.status, 403)
        headers["Host"] = "127.0.0.1"
        response = await self.client.post("/pair", json={}, headers=headers)
        self.assertEqual(response.status, 403)
        self.http.access = AccessPolicy(dict(settings, peers=["192.168.1.2"]))
        headers["Host"] = "video.example"
        response = await self.client.get("/", headers=headers)
        self.assertEqual(response.status, 403)
        self.http.access = AccessPolicy(dict(settings, enabled=False))
        response = await self.client.get("/", headers=headers)
        self.assertEqual(response.status, 403)

    async def test_proxy_preserves_range_and_upstream_agent_without_exposing_url(self):
        await self.pair()
        session = await self.start_playback()
        self.assertNotIn(str(self.upstream.make_url("/video")), str(session))
        response = await self.client.get(session["url"], headers={"Range": "bytes=4-7"})
        self.assertEqual(response.status, 206)
        self.assertEqual(response.headers["Content-Range"], "bytes 4-7/16")
        self.assertEqual(await response.read(), b"4567")
        self.assertEqual(self.requests[-1]["User-Agent"], "Bound-UA")
        self.assertNotIn("Cookie", self.requests[-1])
        response = await self.client.head(session["url"])
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"")
        response = await self.client.get(session["url"], headers={"Range": "bytes=0-2,4-7"})
        self.assertEqual(response.status, 416)

    async def test_replay_does_not_create_another_session_and_late_progress_is_ignored(self):
        await self.pair()
        resolve = self.source.browser_stream
        calls = []
        def counted(*args):
            calls.append(True)
            return resolve(*args)
        self.source.browser_stream = counted
        first = await self.start_playback()
        replay = await self.start_playback()
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(len(calls), 1)
        second = await self.start_playback("two")
        for session, pos in ((second, 200), (first, 400)):
            response = await self.client.post("/api/progress", json={"id": session["id"],
                                              "generation": session["generation"], "pos": pos,
                                              "duration": 600, "paused": True})
            self.assertEqual(response.status, 200)
        self.assertEqual(self.core.library.resume_at(VIDEO), 200)
        response = await self.client.post("/api/seek", json={"id": second["id"], "pos": 300})
        moved = await response.json()
        self.assertEqual(response.status, 200, moved)
        response = await self.client.post("/api/progress", json={"id": second["id"],
                                          "generation": second["generation"], "pos": 1,
                                          "duration": 600, "paused": True})
        self.assertFalse((await response.json())["saved"])
        response = await self.client.get(second["url"])
        self.assertEqual(response.status, 410)

    async def test_failed_replacement_keeps_the_previous_session_and_its_progress(self):
        await self.pair()
        first = await self.start_playback()
        def unavailable(*args):
            raise OSError("unavailable")
        self.source.browser_stream = unavailable
        response = await self.client.post("/api/play", json={"video": VIDEO, "request": "failed",
                                                            "previous": first["id"]})
        self.assertEqual(response.status, 503)
        self.assertEqual(set(self.core.sessions.sessions), {first["id"]})
        self.assertTrue(self.core.sessions.report(first["id"], 100, 600, paused=True)["saved"])

    async def test_revoked_device_cannot_read_or_control_an_existing_session(self):
        device = await self.pair()
        session = await self.start_playback()
        self.pairing.revoke(device)
        self.core.sessions.stop_device(device)
        response = await self.client.get(session["url"])
        self.assertEqual(response.status, 401)
        self.assertEqual(self.core.sessions.sessions, {})
