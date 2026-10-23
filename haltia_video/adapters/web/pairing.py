# SPDX-License-Identifier: GPL-2.0
"""Device grants, persisted by the application's owner, never HTTP workers."""
import hashlib
import ipaddress
import secrets
import socket
import time

from sources import load_json, save_json


def addresses():
    found = {"127.0.0.1"}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            found.add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None,
                                                             socket.AF_INET, socket.SOCK_STREAM))
    except OSError:
        pass
    return sorted(ip for ip in found if ipaddress.ip_address(ip).is_private)


class Pairing:
    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self.data = load_json(path) or {}
        self.data.setdefault("enabled", False)
        self.data.setdefault("port", 8765)
        self.data.setdefault("devices", {})
        self.pending = None

    def save(self):
        save_json(self.path, self.data, private=True)

    def issue(self):
        self.pending = {"secret": secrets.token_urlsafe(32),
                        "code": "%06d" % secrets.randbelow(1000000),
                        "until": self.clock() + 300, "tries": 0}
        return dict(self.pending)

    def pair(self, code, name):
        pending = self.pending
        if not pending or pending["until"] < self.clock() or pending["tries"] >= 5:
            raise ValueError("Код подключения истёк. Получите новый код на ПК.")
        pending["tries"] += 1
        if not (secrets.compare_digest(code, pending["code"]) or
                secrets.compare_digest(code, pending["secret"])):
            raise ValueError("Неверный код подключения.")
        if len(self.data["devices"]) >= 16:
            raise ValueError("Достигнут предел устройств. Отключите ненужное устройство на ПК.")
        token = secrets.token_urlsafe(32)
        device = secrets.token_urlsafe(12)
        self.data["devices"][device] = {"name": str(name or "Браузер")[:80],
                                         "hash": hashlib.sha256(token.encode()).hexdigest(),
                                         "created": self.clock()}
        self.pending = None
        self.save()
        return device, token

    def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        for device, record in self.data["devices"].items():
            if secrets.compare_digest(record["hash"], digest):
                return device
        return None

    def devices(self):
        return [{"id": ident, "name": r["name"], "created": r["created"]}
                for ident, r in self.data["devices"].items()]

    def revoke(self, device):
        self.data["devices"].pop(device, None)
        self.save()
