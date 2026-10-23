# SPDX-License-Identifier: GPL-2.0
"""Legacy cache constructor with the desktop JSON adapter."""
from haltia_video.core.kept import FORMAT, RETRY_SEC, Kept as _Kept
from sources import load_json, save_json


class Kept(_Kept):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("load", load_json)
        kwargs.setdefault("save", save_json)
        super().__init__(*args, **kwargs)
