# SPDX-License-Identifier: GPL-2.0
"""Recorded answers, the second adapter at the sources' http seam (the
first is sources.net): a scenario is a JSON list of exchanges, each
{"url", "params", "answer"}, answered in order. tests/record.py writes
them from the live services."""
import json
import os
import urllib.parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
# Not part of what an exchange is about: they change on every recording.
NOISE = {"access_token", "v", "lang", "client_id", "client_secret", "auth_hash",
         "context"}


def method_of(url):
    """The VK method of an API URL, or the URL without its query."""
    parts = urllib.parse.urlsplit(url)
    if parts.netloc == "api.vk.com":
        return parts.path.rsplit("/", 1)[-1]
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)


def load(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def exchange(url, answer, **params):
    """One exchange written by hand, for what cannot be recorded."""
    return {"url": url, "params": {k: str(v) for k, v in params.items()}, "answer": answer}


def key_params(data):
    return {k: str(v) for k, v in (data or {}).items() if k not in NOISE}


class Replay:
    """Answers the recorded exchanges of a scenario in order, and fails
    a test that asks something else or leaves some unasked."""

    def __init__(self, *scenarios, exchanges=()):
        self.queue = []
        for name in scenarios:
            self.queue.extend(load(name))
        self.queue.extend(exchanges)
        self.asked = []  # (method, params as sent, tokens included)

    def _next(self, url, data):
        what = method_of(url)
        self.asked.append((what, dict(data or {})))
        if not self.queue:
            raise AssertionError("not recorded: %s %s" % (what, key_params(data)))
        ex = self.queue.pop(0)
        if ex["url"] != what or ex["params"] != key_params(data):
            raise AssertionError("asked %s %s, recorded %s %s"
                                 % (what, key_params(data), ex["url"], ex["params"]))
        return ex["answer"]

    def get_json(self, url, data=None, headers=None):
        return self._next(url, data)

    def request(self, url, data=None, headers=None, limit=None):
        answer = self._next(url, data)
        return answer.encode("utf-8") if isinstance(answer, str) else answer

    def done(self):
        return not self.queue
