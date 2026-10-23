# SPDX-License-Identifier: GPL-2.0
"""Explicit public origin and socket peers; forwarded headers confer no trust."""
import ipaddress
import re
from urllib.parse import urlsplit


DEFAULT_PEERS = "127.0.0.1, ::1"


def origin(value):
    value = str(value).strip()
    if not value or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("Укажите внешний адрес: https://video.example.org")
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise ValueError("Некорректный адрес или порт.")
    if (parts.scheme not in ("http", "https") or not host or parts.username is not None or
            parts.password is not None or parts.path not in ("", "/") or
            "?" in value or "#" in value or parts.netloc.endswith(":")):
        raise ValueError("Нужен адрес HTTP/HTTPS без пути, параметров и пароля.")
    try:
        address = ipaddress.ip_address(host)
        host = "[%s]" % address if address.version == 6 else str(address)
    except ValueError:
        host = host.encode("idna").decode("ascii").lower()
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                      for label in host.split(".")):
            raise ValueError("Некорректное имя сервера.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Некорректный порт.")
    suffix = ":%d" % port if port and port != {"http": 80, "https": 443}[parts.scheme] else ""
    return "%s://%s%s" % (parts.scheme, host, suffix)


def proxy_settings(enabled=False, url="", peers=DEFAULT_PEERS):
    url = origin(url) if str(url).strip() else ""
    if isinstance(peers, str):
        peers = peers.replace(",", " ").split()
    try:
        peers = list(dict.fromkeys(str(ipaddress.ip_address(p)) for p in peers))
    except (ValueError, TypeError):
        raise ValueError("Укажите IP-адреса прокси через запятую, без подсетей и имён.")
    if len(peers) > 16:
        raise ValueError("Можно указать не больше 16 адресов прокси.")
    if enabled and (not url or not peers):
        raise ValueError("Для публикации нужны внешний адрес и IP-адрес прокси.")
    return {"enabled": bool(enabled), "url": url, "peers": peers}


class AccessPolicy:
    def __init__(self, settings=None):
        settings = proxy_settings(**(settings or {}))
        self.external = settings["url"] if settings["enabled"] else None
        self.peers = frozenset(settings["peers"])

    def request_origin(self, peer, scheme, host):
        """Return the allowed browser origin, or reject the socket/Host pair.
        HTTPS termination comes from the configured URL, never a client header.
        """
        try:
            peer = ipaddress.ip_address(peer)
            candidate = origin("%s://%s" % (scheme, host))
            if self.external:
                external = urlsplit(self.external)
                if origin("%s://%s" % (external.scheme, host)) == self.external:
                    if str(peer) not in self.peers:
                        raise PermissionError("Запрос пришёл не от доверенного прокси.")
                    return self.external
            hostname = urlsplit(candidate).hostname
            if not (peer.is_private or peer.is_loopback):
                raise PermissionError("Разрешены локальные соединения или настроенный прокси.")
            if hostname != "localhost":
                address = ipaddress.ip_address(hostname)
                if not (address.is_private or address.is_loopback):
                    raise PermissionError("Адрес сервера не разрешён.")
            return candidate
        except ValueError:
            raise PermissionError("Адрес сервера не разрешён.")
