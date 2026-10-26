# SPDX-License-Identifier: GPL-2.0
"""Platform defaults; application services receive explicit directories."""
import os
import sys


def defaults():
    if os.name == "nt":
        data = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
        cache = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        return tuple(os.path.join(p, "haltia-video") for p in (data, data, cache))
    if sys.platform == "darwin":
        data = os.path.expanduser("~/Library/Application Support/haltia-video")
        return data, data, os.path.expanduser("~/Library/Caches/haltia-video")
    return tuple(os.path.join(os.environ.get(var) or os.path.expanduser(default), "haltia-video")
                 for var, default in (("XDG_CONFIG_HOME", "~/.config"),
                                      ("XDG_DATA_HOME", "~/.local/share"),
                                      ("XDG_CACHE_HOME", "~/.cache")))
