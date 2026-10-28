# SPDX-License-Identifier: GPL-2.0
"""HTTP for the sources and the card pictures: a kept-alive connection per
host and thread (a TLS handshake costs a few hundred milliseconds on the
tablet's CPU, and a page of cards or a feed of channels asks one host
dozens of times) and gzip for the large JSON and XML answers. Not a
source: it defines no SOURCE."""
import gzip
import json
import threading
import urllib.parse
import zlib

UA = "Mozilla/5.0 (X11; Linux armv7l; rv:151.0) Gecko/20100101 Firefox/151.0"
TIMEOUT = 20
MAX_BYTES = 8 * 1024 * 1024
REDIRECTS = 5

_local = threading.local()


class HTTPError(OSError):
    def __init__(self, status, url):
        super().__init__("HTTP %s: %s" % (status, url))
        self.status = status


def _conn(scheme, host, fresh=False):
    # http.client (with ssl and the email package) is imported by the first
    # request, in a worker, not before the first window.
    import http.client

    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    key = (scheme, host)
    if fresh or key not in conns:
        old = conns.pop(key, None)
        if old:
            old.close()
        cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        conns[key] = cls(host, timeout=TIMEOUT)
    return conns[key]


def request(url, data=None, headers=None, limit=MAX_BYTES):
    """The body of url (POSTed data, a dict, when given: as a form, or as
    JSON when headers say so), unpacked."""
    import http.client

    hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    body = None
    if data is not None and hdrs.get("Content-Type") == "application/json":
        body = json.dumps(data).encode()
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    for _hop in range(REDIRECTS):
        parts = urllib.parse.urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        # A kept connection the server has closed fails at once: then one
        # more try on a new one.
        for fresh in (False, True):
            conn = _conn(parts.scheme, parts.netloc, fresh)
            try:
                conn.request("POST" if body is not None else "GET", path, body, hdrs)
                resp = conn.getresponse()
                raw = resp.read(limit + 1)
                break
            except (http.client.HTTPException, ConnectionError, BrokenPipeError):
                if fresh:
                    raise
            except OSError:
                _conn(parts.scheme, parts.netloc, True)  # do not keep a broken one
                raise
        if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
            url = urllib.parse.urljoin(url, resp.getheader("Location"))
            if resp.status == 303:
                body = None
            continue
        if resp.status >= 400:
            raise HTTPError(resp.status, url)
        if len(raw) > limit:
            raise OSError("too large: " + url)
        enc = (resp.getheader("Content-Encoding") or "").lower()
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw)
        return raw
    raise OSError("too many redirects: " + url)


def get_json(url, data=None, headers=None):
    return json.loads(request(url, data, headers))
