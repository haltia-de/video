# SPDX-License-Identifier: GPL-2.0
"""HTTP and SSE adapter. Imported only when LAN access is enabled."""
import asyncio
from concurrent.futures import Future
import ipaddress
import json
from pathlib import Path
import secrets
import time

from aiohttp import web

from .media import Media

COOKIE = "haltia_device"
ASSETS = Path(__file__).with_name("static")


class WebApplication:
    def __init__(self, controller, core):
        self.controller = controller
        self.core = core
        self.stopped = asyncio.Event()
        self.subscribers = {}
        self.media = Media(self)
        self.runner = None
        self.app = web.Application(client_max_size=2 * 1024 * 1024,
                                   middlewares=[self.guard])
        self.app.router.add_get("/", self.index)
        for name in ("app.js", "app.css"):
            self.app.router.add_get("/" + name, self.asset)
        self.app.router.add_post("/pair", self.pair)
        self.app.router.add_get("/events", self.events)
        self.app.router.add_get("/qr", self.qr)
        self.app.router.add_post("/api/query", self.query)
        self.app.router.add_post("/api/command", self.command)
        self.app.router.add_post("/api/bookmark-link", self.bookmark_link)
        self.app.router.add_post("/api/cookies", self.cookies)
        self.app.router.add_post("/api/play", self.play)
        self.app.router.add_post("/api/progress", self.progress)
        self.app.router.add_post("/api/seek", self.seek)
        self.app.router.add_post("/api/stop", self.stop)
        self.app.router.add_route("GET", "/media/{session}/{generation}", self.media.serve)
        self.app.router.add_route("HEAD", "/media/{session}/{generation}", self.media.serve)
        self.app.router.add_route("GET", "/track/{session}/{track}", self.media.track)
        self.app.router.add_route("HEAD", "/track/{session}/{track}", self.media.track)

    async def owner(self, fn, *args):
        future = self.core.runtime.dispatch(fn, *args)
        # Disconnecting a request must not cancel an accepted mutation.
        return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), 10)

    async def work(self, fn, *args, valid=None):
        future = Future()

        def begin():
            def done(value, error):
                if future.done():
                    return
                if valid and not valid():
                    error = ValueError("Сеанс или аккаунт изменился.")
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(value)
            self.core.work(fn, *args, done=done)
        await self.owner(begin)
        return await asyncio.wait_for(asyncio.wrap_future(future), 60)

    @web.middleware
    async def guard(self, request, handler):
        try:
            remote = ipaddress.ip_address(request.remote or "127.0.0.1")
            if not (remote.is_private or remote.is_loopback):
                raise web.HTTPForbidden()
            host = request.host.rsplit(":", 1)[0].strip("[]")
            if host != "localhost":
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    raise web.HTTPForbidden()
                if not (address.is_private or address.is_loopback):
                    raise web.HTTPForbidden()
            internal = (request.path.startswith("/track/") and remote.is_loopback and
                        secrets.compare_digest(request.query.get("key", ""), self.controller.internal_key))
            if request.method not in ("GET", "HEAD"):
                origin = request.headers.get("Origin")
                if origin != "%s://%s" % (request.scheme, request.host):
                    raise web.HTTPForbidden(text="Запрос должен исходить из интерфейса приложения.")
            public = request.path in ("/", "/app.js", "/app.css", "/pair")
            if not public and not internal:
                token = request.cookies.get(COOKIE, "")
                device = await self.owner(self.controller.pairing.authenticate, token)
                if not device:
                    raise web.HTTPUnauthorized(text="Подключите браузер по коду с ПК.")
                request["device"] = device
            request["internal"] = internal
            response = await handler(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Content-Security-Policy",
                                        "default-src 'self'; img-src 'self' https: http: data:; "
                                        "media-src 'self' blob:; style-src 'self'; script-src 'self'; "
                                        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            return response
        except web.HTTPException:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            return web.json_response({"error": str(exc)[:300]}, status=400)
        except asyncio.TimeoutError:
            return web.json_response({"error": "Запрос занял слишком много времени. Повторите попытку."}, status=503)
        except Exception:
            # Extractor errors can contain signed URLs or cookies.
            return web.json_response({"error": "Не удалось выполнить запрос. Проверьте доступ и соединение."}, status=503)

    async def payload(self, request):
        if request.content_length is not None and request.content_length > 65536:
            raise web.HTTPRequestEntityTooLarge(max_size=65536, actual_size=request.content_length)
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Ожидается объект запроса.")
        return data

    async def index(self, request):
        return web.FileResponse(ASSETS / "index.html")

    async def asset(self, request):
        return web.FileResponse(ASSETS / request.path[1:])

    async def qr(self, request):
        def current():
            sign = self.core.signins.get(request.query.get("source"))
            if not sign or sign.state["kind"] != "waiting":
                raise ValueError("Нет ожидающего подтверждения.")
            return sign.state["url"]
        url = await self.owner(current)

        def encode():
            import io
            import qrcode
            out = io.BytesIO()
            qrcode.make(url, box_size=5, border=2).save(out, format="PNG")
            return out.getvalue()
        return web.Response(body=await self.work(encode), content_type="image/png")

    async def pair(self, request):
        p = await self.payload(request)
        device, token = await self.owner(self.controller.pairing.pair,
                                         str(p.get("code", ""))[:100], p.get("name", "Браузер"))
        await self.owner(self.controller.changed)
        response = web.json_response({"ok": True, "device": device})
        response.set_cookie(COOKIE, token, httponly=True, samesite="Strict", max_age=10 * 365 * 86400)
        return response

    async def query(self, request):
        p = await self.payload(request)
        return web.json_response(await self.owner(self.core.query, p["view"], p.get("params"), request["device"]))

    async def command(self, request):
        p = await self.payload(request)
        return web.json_response(await self.owner(self.core.command, p["name"], p.get("params"), request["device"]))

    async def bookmark_link(self, request):
        p = await self.payload(request)
        url = str(p.get("url", ""))[:2048]

        def prepare():
            src = next((s for s in self.core.sources.values() if hasattr(s, "resolve_bookmark") and s.owns(url)), None)
            if not src:
                raise ValueError("Нужна ссылка поддерживаемого видеосервиса.")
            return src, self.core.epoch
        src, epoch = await self.owner(prepare)
        kind, obj = await self.work(src.resolve_bookmark, url, valid=lambda: self.core.epoch == epoch)
        result = await self.owner(self.core.command, "bookmark_add", {"kind": kind, "entity": obj}, request["device"])
        return web.json_response(result)

    async def cookies(self, request):
        # File selection is explicit; the browser's own cookies are never read.
        source = request.query.get("source", "youtube")
        data = await request.read()
        if len(data) > 2 * 1024 * 1024 or b"\0" in data:
            raise ValueError("Нужен текстовый файл cookies.txt размером до 2 МБ.")

        def prepare():
            src = self.core.sources.get(source)
            if not src or getattr(src, "login", None) != "cookies":
                raise ValueError("Источник не принимает cookies.txt.")
            self.core.signins[source].cancel()
            return src
        src = await self.owner(prepare)

        def store():
            import os
            fd = os.open(src.cookies, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        await self.work(store)
        return web.json_response(await self.owner(self.core.command, "signin", {"source": source}, request["device"]))

    async def events(self, request):
        device = request["device"]
        if sum(d == device for d, q in self.subscribers.values()) >= 2:
            raise web.HTTPServiceUnavailable(text="Слишком много открытых вкладок.")
        queue = asyncio.Queue(maxsize=16)
        ident = secrets.token_hex(8)
        self.subscribers[ident] = (device, queue)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
        await response.prepare(request)
        await response.write(b"data: {\"type\":\"resync\"}\n\n")
        try:
            while not self.stopped.is_set():
                try:
                    event = await asyncio.wait_for(queue.get(), 15)
                    await response.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
                    if event["type"] == "shutdown" or (event["type"] == "device_revoked" and
                                                       event["data"]["device"] == device):
                        break
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.subscribers.pop(ident, None)
        return response

    def publish(self, event):
        if event["type"] == "playback_stopped":
            self.media.cancel(event["data"]["id"])
        for device, queue in list(self.subscribers.values()):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "resync", "revision": event.get("revision")})
            else:
                queue.put_nowait(event)

    async def play(self, request):
        p = await self.payload(request)
        device = request["device"]

        def begin():
            nonce = str(p.get("request", ""))[:100]
            existing = self.core.play_requests.get((device, nonce)) if nonce else None
            if existing and existing in self.core.sessions.sessions:
                return {"id": existing}
            plan = self.core.playback_plan(p.get("video"), p.get("playlist"), p.get("order"),
                                            bool(p.get("resume")), bool(p.get("start_over")), device)
            if plan is None:
                return None
            previous = p.get("previous")
            if previous and previous in self.core.sessions.sessions:
                self.core.sessions.get(previous, device)
            session = self.core.sessions.start(device, plan["video"], plan["playlist"], plan["queue"],
                                                plan["start_over"], activate=False)
            self.core.sessions.get(session["id"])["order"] = plan["order"]
            if nonce:
                self.core.play_requests[(device, nonce)] = session["id"]
            return session
        session = await self.owner(begin)
        if session is None:
            return web.json_response({"loading": True})
        try:
            result = await self.media.prepare(session["id"], device, p.get("quality", 720))
        except BaseException:
            if not self.stopped.is_set():
                await self.owner(self.core.sessions.stop, session["id"])
            raise
        # A failed resolution must leave the previous player usable.
        def replace():
            previous = p.get("previous")
            if previous and previous != session["id"] and previous in self.core.sessions.sessions:
                self.core.sessions.get(previous, device)
                self.core.sessions.stop(previous)
        await self.owner(replace)
        return web.json_response(result)

    async def progress(self, request):
        p = await self.payload(request)

        def report():
            s = self.core.sessions.get(p["id"], request["device"])
            if int(p.get("generation", -1)) != s["generation"]:
                return {"saved": False}
            return self.core.sessions.report(s["id"], p.get("pos", 0), p.get("duration"),
                                              bool(p.get("paused")), bool(p.get("ended")),
                                              bool(p.get("force")))
        return web.json_response(await self.owner(report))

    async def seek(self, request):
        p = await self.payload(request)
        result = await self.media.seek(p["id"], request["device"], p.get("pos", 0), p.get("quality"))
        return web.json_response(result)

    async def stop(self, request):
        p = await self.payload(request)

        def stop():
            self.core.sessions.get(p["id"], request["device"])
            self.core.sessions.stop(p["id"])
        await self.owner(stop)
        return web.json_response({"ok": True})

    async def start(self, port):
        await self.media.start()
        self.runner = web.AppRunner(self.app, handle_signals=False, access_log=None, shutdown_timeout=2)
        await self.runner.setup()
        try:
            site = web.TCPSite(self.runner, "0.0.0.0", port)
            await site.start()
        except BaseException:
            await self.close()
            raise

    async def close(self):
        self.stopped.set()
        for device, queue in self.subscribers.values():
            if not queue.full():
                queue.put_nowait({"type": "shutdown", "data": {}})
        await self.media.close()
        if self.runner:
            await self.runner.cleanup()
