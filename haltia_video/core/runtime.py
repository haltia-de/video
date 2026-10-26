# SPDX-License-Identifier: GPL-2.0
"""Bounded work and dispatch to the application's serialized owner.

post(fn) and later(ms, fn) belong to the host; neither requires GLib.
An accepted command completes even when its HTTP caller disconnects.
"""
from concurrent.futures import Future, ThreadPoolExecutor
import threading


class Busy(Exception):
    pass


class Runtime:
    def __init__(self, post, later, workers=6, pending=24):
        self.post = post
        self.later = later
        self.owner = threading.get_ident()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-work")
        self.slots = threading.BoundedSemaphore(pending)
        self.commands = threading.BoundedSemaphore(128)
        self.closed = False

    def dispatch(self, fn, *args):
        future = Future()
        if self.closed or not self.commands.acquire(False):
            future.set_exception(Busy("Приложение занято или закрывается."))
            return future

        def invoke():
            try:
                if self.closed:
                    raise Busy("Приложение закрывается.")
                future.set_result(fn(*args))
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self.commands.release()
        if threading.get_ident() == self.owner:
            invoke()
        else:
            self.post(invoke)
        return future

    def work(self, fn, *args, done):
        if self.closed or not self.slots.acquire(False):
            self.post(lambda: done(None, Busy("Приложение занято. Повторите запрос.")))
            return

        def worker():
            try:
                result, error = fn(*args), None
            except Exception as exc:
                result, error = None, exc
            finally:
                self.slots.release()
            if not self.closed:
                self.post(lambda: done(result, error) if not self.closed else None)
        try:
            self.executor.submit(worker)
        except RuntimeError:
            self.slots.release()
            if not self.closed:
                self.post(lambda: done(None, Busy("Приложение закрывается.")))

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
