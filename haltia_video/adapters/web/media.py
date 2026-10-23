# SPDX-License-Identifier: GPL-2.0
"""Bounded range proxy and optional stream-copy delivery for web players.

No extraction, byte transfer or subprocess wait runs on the GTK owner.
Separate tracks use a seekable upstream proxy and fragmented MP4 output.
The client supplies a timeline slider; seeking replaces that stream with
one starting at the requested time, rather than inventing byte ranges.
"""
import asyncio
import copy
import math
import re
import shutil

import aiohttp
from aiohttp import web

RANGE = re.compile(r"bytes=(?:\d+-\d*|-\d+)\Z")


class Media:
    def __init__(self, application):
        self.application = application
        self.controller = application.controller
        self.core = application.core
        self.http = None
        self.descriptors = {}
        self.preparing = {}
        self.tasks = {}
        self.upstreams = asyncio.Semaphore(8)
        self.remuxes = asyncio.Semaphore(2)
        self.ffmpeg = shutil.which("ffmpeg")

    async def start(self):
        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=20),
            connector=aiohttp.TCPConnector(limit=12, limit_per_host=8), auto_decompress=False)

    def cancel(self, ident):
        for task in list(self.tasks.get(ident, ())):
            task.cancel()
        self.descriptors.pop(ident, None)

    async def close(self):
        tasks = ([task for tasks in self.tasks.values() for task in tasks] +
                 list(self.preparing.values()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.http:
            await self.http.close()
        self.descriptors.clear()
        self.preparing.clear()

    async def prepare(self, ident, device, quality=720):
        quality = max(144, min(int(quality), 2160))
        generation = await self.application.owner(
            lambda: self.core.sessions.get(ident, device)["generation"])
        descriptor = self.descriptors.get(ident)
        if descriptor and descriptor["generation"] == generation and descriptor["quality"] == quality:
            return await self.public(ident, device)
        key = (ident, generation, quality)
        task = self.preparing.get(key)
        if task is None:
            task = asyncio.create_task(self._prepare(ident, device, quality))
            self.preparing[key] = task
            task.add_done_callback(lambda finished: self.preparing.pop(key, None))
        return await asyncio.shield(task)

    async def _prepare(self, ident, device, quality):

        def capture():
            s = self.core.sessions.get(ident, device)
            source = self.core.sources[s["video"]["source"]]
            if not hasattr(source, "browser_stream"):
                raise ValueError("Источник пока не поддерживает браузерный плеер.")
            return source, copy.deepcopy(s["video"]), self.core.epoch, s["generation"]
        source, video, epoch, generation = await self.application.owner(capture)
        fmt = ("bv*[height<=%d][vcodec^=avc1]+ba[ext=m4a]/"
               "b[height<=%d][vcodec^=avc1][acodec^=mp4a]" % (quality, quality))

        def valid():
            s = self.core.sessions.sessions.get(ident)
            return s is not None and self.core.epoch == epoch and s["generation"] == generation
        descriptor = await self.application.work(source.browser_stream, video, fmt, valid=valid)
        tracks = descriptor.get("tracks") or []
        if not tracks or len(tracks) > 2:
            raise ValueError("Нет совместимых дорожек. Откройте видео в сервисе.")
        for track in tracks:
            if not isinstance(track.get("url"), str) or not track["url"].startswith(("http://", "https://")):
                raise ValueError("Источник не вернул HTTP-поток.")
            if track.get("protocol") not in (None, "http", "https", "http_dash_segments"):
                raise ValueError("Этот поток пока недоступен в браузере. Откройте видео в сервисе.")
        # Segmented DASH needs an index/segment adapter, not one GET.
        if any(t.get("protocol") == "http_dash_segments" for t in tracks):
            raise ValueError("Сегментированный поток пока не поддерживается. Откройте видео в сервисе.")
        mode = "direct" if len(tracks) == 1 and tracks[0].get("container") == "mp4" else "remux"
        if mode == "remux" and not self.ffmpeg:
            raise ValueError("Для этого видео нужен FFmpeg. Откройте видео в сервисе или установите веб-компоненты.")
        descriptor.update(mode=mode, quality=quality, generation=generation)
        self.descriptors[ident] = descriptor
        await self.application.owner(self.core.sessions.activate, ident)
        return await self.public(ident, device)

    async def public(self, ident, device):
        d = self.descriptors[ident]

        def snapshot():
            s = self.core.sessions.get(ident, device)
            queue = s["queue"]
            index = next((i for i, v in enumerate(queue) if v["source"] == s["video"]["source"] and
                          v["id"] == s["video"]["id"]), None)
            return {"id": ident, "generation": s["generation"], "video": copy.deepcopy(s["video"]),
                    "playlist": copy.deepcopy(s["playlist"]), "pos": s["pos"],
                    "order": s.get("order"),
                    "duration": d.get("duration") or s["duration"],
                    "previous": copy.deepcopy(queue[index - 1]) if index is not None and index > 0 else None,
                    "next": copy.deepcopy(queue[index + 1]) if index is not None and index + 1 < len(queue) else None,
                    "service_url": self.core.sources[s["video"]["source"]].watch_url(s["video"])}
        result = await self.application.owner(snapshot)
        result.update(url="/media/%s/%d" % (ident, result["generation"]),
                      mode=d["mode"], quality=d["quality"], height=d.get("height"),
                      offset=result["pos"] if d["mode"] == "remux" else 0,
                      mime="video/mp4")
        return result

    async def seek(self, ident, device, pos, quality=None):
        if not isinstance(pos, (int, float)) or not math.isfinite(pos) or not 0 <= pos <= 1e9:
            raise ValueError("Некорректная позиция.")
        d = self.descriptors.get(ident)
        if d is None:
            raise ValueError("Сеанс закрыт.")

        def update():
            s = self.core.sessions.get(ident, device)
            if s["duration"]:
                s["pos"] = min(pos, s["duration"])
            else:
                s["pos"] = pos
            s["generation"] += 1
            return s["generation"]
        generation = await self.application.owner(update)
        for task in list(self.tasks.get(ident, ())):
            task.cancel()
        if quality is not None and int(quality) != d["quality"]:
            return await self.prepare(ident, device, quality)
        d["generation"] = generation
        return await self.public(ident, device)

    async def session(self, request, ident):
        def get():
            s = self.core.sessions.get(ident, None if request.get("internal") else request["device"])
            return {"generation": s["generation"], "pos": s["pos"]}
        s = await self.application.owner(get)
        d = self.descriptors.get(ident)
        if not d:
            raise web.HTTPGone()
        return s, d

    async def serve(self, request):
        ident = request.match_info["session"]
        session, descriptor = await self.session(request, ident)
        if str(session["generation"]) != request.match_info["generation"]:
            raise web.HTTPGone()
        task = asyncio.current_task()
        self.tasks.setdefault(ident, set()).add(task)
        try:
            if descriptor["mode"] == "direct":
                return await self.proxy(request, descriptor["tracks"][0])
            return await self.remux(request, ident, session, descriptor)
        finally:
            self.tasks.get(ident, set()).discard(task)
            if not self.tasks.get(ident):
                self.tasks.pop(ident, None)

    async def track(self, request):
        if not request.get("internal"):
            raise web.HTTPForbidden()
        ident = request.match_info["session"]
        session, descriptor = await self.session(request, ident)
        index = int(request.match_info["track"])
        if not 0 <= index < len(descriptor["tracks"]):
            raise web.HTTPNotFound()
        task = asyncio.current_task()
        self.tasks.setdefault(ident, set()).add(task)
        try:
            return await self.proxy(request, descriptor["tracks"][index])
        finally:
            self.tasks.get(ident, set()).discard(task)

    async def proxy(self, request, track):
        byte_range = request.headers.get("Range")
        if byte_range and not RANGE.fullmatch(byte_range):
            raise web.HTTPRequestRangeNotSatisfiable()
        headers = {str(k): str(v) for k, v in (track.get("headers") or {}).items()
                   if k.lower() in ("user-agent", "referer", "origin", "cookie", "authorization")}
        headers["Accept-Encoding"] = "identity"
        if byte_range:
            headers["Range"] = byte_range
        try:
            await asyncio.wait_for(self.upstreams.acquire(), 3)
        except asyncio.TimeoutError:
            raise web.HTTPServiceUnavailable(text="Все потоки заняты. Повторите попытку.")
        try:
            async with self.http.request(request.method, track["url"], headers=headers,
                                         allow_redirects=True) as upstream:
                if upstream.status not in (200, 206, 416):
                    raise web.HTTPBadGateway(text="Источник не отдал видео. Откройте его заново.")
                out = {k: v for k, v in upstream.headers.items() if k.lower() in
                       ("content-type", "content-range", "content-length", "accept-ranges")}
                out.setdefault("Content-Type", "video/mp4" if track.get("container") == "mp4" else "application/octet-stream")
                out["Cache-Control"] = "no-store"
                response = web.StreamResponse(status=upstream.status, headers=out)
                await response.prepare(request)
                if request.method != "HEAD":
                    async for chunk in upstream.content.iter_chunked(65536):
                        await response.write(chunk)
                return response
        finally:
            self.upstreams.release()

    async def remux(self, request, ident, session, descriptor):
        if request.method == "HEAD":
            return web.Response(headers={"Content-Type": "video/mp4", "Accept-Ranges": "none"})
        try:
            await asyncio.wait_for(self.remuxes.acquire(), 2)
        except asyncio.TimeoutError:
            raise web.HTTPServiceUnavailable(text="Все медиасеансы заняты. Повторите попытку.")
        proc = None
        try:
            args = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1"]
            for index, track in enumerate(descriptor["tracks"]):
                if session["pos"]:
                    args += ["-ss", str(session["pos"])]
                url = "http://127.0.0.1:%d/track/%s/%d?key=%s" % (
                    self.controller.port, ident, index, self.controller.internal_key)
                args += ["-i", url]
            if len(descriptor["tracks"]) == 2:
                for index, track in enumerate(descriptor["tracks"]):
                    args += ["-map", "%d:%s:0" % (index, "a" if track["kind"] == "audio" else "v")]
            args += ["-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags",
                     "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.DEVNULL)
            first = await asyncio.wait_for(proc.stdout.read(65536), 20)
            if not first:
                raise web.HTTPBadGateway(text="Не удалось объединить дорожки. Откройте видео в сервисе.")
            response = web.StreamResponse(headers={"Content-Type": "video/mp4", "Cache-Control": "no-store",
                                                    "Accept-Ranges": "none"})
            await response.prepare(request)
            await response.write(first)
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                await response.write(chunk)
            return response
        finally:
            if proc is not None:
                if proc.returncode is None:
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            self.remuxes.release()
