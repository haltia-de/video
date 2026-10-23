# SPDX-License-Identifier: GPL-2.0
"""Single-writer JSON storage, retaining the existing desktop file layout."""
import json
import os


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


class JsonStore:
    def __init__(self, config_dir, data_dir):
        self.paths = {"config": os.path.join(config_dir, "video.json"),
                      "history": os.path.join(data_dir, "history.json"),
                      "playback": os.path.join(data_dir, "playback.json")}

    def load(self, name):
        return load_json(self.paths[name])

    def save(self, name, data):
        save_json(self.paths[name], data)
