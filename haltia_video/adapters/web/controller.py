# SPDX-License-Identifier: GPL-2.0
"""Owner-thread lifecycle of the optional server. asyncio stays off GTK."""
import os
import secrets
import threading

from .pairing import Pairing, addresses
from .access import proxy_settings


class Server:
    def __init__(self, core, config_dir, changed):
        self.core = core
        self.pairing = Pairing(os.path.join(config_dir, "web.json"))
        self.changed = changed
        self.state = "off"
        self.note = ""
        self.loop = None
        self.application = None
        self.thread = None
        self.generation = 0
        self.unwatch = None
        self.restart_requested = False
        self.stop_requested = threading.Event()
        self.internal_key = None
        self.port = self.pairing.data["port"]
        self.ips = ["127.0.0.1"]

    def snapshot(self):
        urls = ["http://%s:%d/" % (ip, self.port) for ip in self.ips]
        proxy = dict(self.pairing.data["proxy"])
        proxy["peers"] = list(proxy["peers"])
        if proxy["enabled"]:
            urls.insert(0, proxy["url"] + "/")
        return {"state": self.state, "note": self.note, "enabled": self.pairing.data["enabled"],
                "port": self.port, "urls": urls, "proxy": proxy, "devices": self.pairing.devices()}

    def configure_proxy(self, enabled, url, peers):
        if self.state not in ("off", "failed"):
            raise ValueError("Перед изменением публикации выключите веб-доступ.")
        settings = proxy_settings(enabled, url, peers)
        self.pairing.data["proxy"] = settings
        self.pairing.pending = None
        self.pairing.save()
        self.changed()

    def issue(self):
        if self.state != "on":
            raise ValueError("Сначала включите доступ из локальной сети.")
        pending = self.pairing.issue()
        self.changed()
        return pending

    def revoke(self, device):
        self.pairing.revoke(device)
        self.core.sessions.stop_device(device)
        self.broadcast({"type": "device_revoked", "data": {"device": device},
                        "revision": self.core.revision})
        self.changed()

    def enable(self, on, port=None):
        if port is not None:
            port = int(port)
            if not 1024 <= port <= 65535:
                raise ValueError("Порт должен быть от 1024 до 65535.")
            if self.state not in ("off", "failed"):
                raise ValueError("Перед изменением порта выключите веб-доступ.")
            self.pairing.data["port"] = port
        self.pairing.data["enabled"] = bool(on)
        self.pairing.save()
        if on:
            self.start()
        else:
            self.stop()

    def start(self):
        if self.state == "stopping":
            self.restart_requested = True
            return
        if self.state in ("starting", "on", "stopping"):
            return
        self.generation += 1
        self.stop_requested.clear()
        generation = self.generation
        self.internal_key = secrets.token_urlsafe(32)
        self.port = self.pairing.data["port"]
        self.state, self.note = "starting", "Запускаю сервер…"
        self.changed()

        def run():
            import asyncio
            try:
                ips = addresses()
                from .http import WebApplication
                asyncio.run(self._serve(WebApplication, generation, ips))
            except Exception:
                self.core.runtime.post(lambda: self._failed(generation))
        # Let asyncio release streams and reap FFmpeg before process exit.
        self.thread = threading.Thread(target=run, name="video-web", daemon=False)
        self.thread.start()

    async def _serve(self, factory, generation, ips):
        import asyncio
        self.loop = asyncio.get_running_loop()
        application = factory(self, self.core)
        self.application = application
        started = False
        try:
            await application.start(self.port)
            started = True
            if self.stop_requested.is_set():
                application.stopped.set()
            else:
                self.core.runtime.post(lambda: self._ready(generation, ips))
            await application.stopped.wait()
        finally:
            await application.close()
            if started:
                self.core.runtime.post(lambda: self._ended(generation))

    def _ready(self, generation, ips):
        if generation != self.generation or self.state != "starting":
            self._request_stop()
            return
        self.state, self.note = "on", "Откройте адрес или отсканируйте QR-код на планшете."
        self.ips = ips
        self.unwatch = self.core.watch(self.broadcast)
        self.changed()

    def _failed(self, generation):
        if generation == self.generation:
            self._unwatch()
            self.state, self.note = "failed", "Не удалось запустить сервер. Проверьте порт и установку веб-компонентов."
            self.application = None
            self.loop = None
            self.changed()

    def _ended(self, generation):
        if generation == self.generation:
            self._unwatch()
            self.state = "off"
            self.application = None
            self.loop = None
            self.changed()
            if self.restart_requested and not self.core.closed:
                self.restart_requested = False
                self.start()

    def broadcast(self, event):
        if self.loop and self.application:
            try:
                self.loop.call_soon_threadsafe(self.application.publish, event)
            except RuntimeError:
                pass

    def stop(self):
        self.restart_requested = False
        self.stop_requested.set()
        self._unwatch()
        self.pairing.pending = None
        for ident, s in list(self.core.sessions.sessions.items()):
            if s["device"] != "gtk":
                self.core.sessions.stop(ident)
        if self.loop and self.application:
            self.state = "stopping"
            self.broadcast({"type": "shutdown", "data": {}, "revision": self.core.revision})
            self._request_stop()
        elif self.state == "starting":
            self.state = "stopping"
        else:
            self.state = "off"
        self.changed()

    def _unwatch(self):
        if self.unwatch:
            self.unwatch()
            self.unwatch = None

    def _request_stop(self):
        if self.loop and self.application:
            try:
                self.loop.call_soon_threadsafe(self.application.stopped.set)
            except RuntimeError:
                pass  # The loop ended before its owner notification arrived.
