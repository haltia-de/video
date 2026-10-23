# SPDX-License-Identifier: GPL-2.0
"""Desktop Library constructor, preserving pre-refactor imports and paths."""
from haltia_video.core.library import (DETAILS_MAX_AGE, DONE_TAIL, HISTORY_MAX, PLAYBACK_MAX,
                                       RESUME_MIN, SCOPES_ALL, SORTS, Library as _Library)
from haltia_video.adapters.storage.json_store import JsonStore
from sources import CONFIG_DIR, DATA_DIR


class Library(_Library):
    def __init__(self, config_dir=None, data_dir=None):
        store = JsonStore(config_dir or CONFIG_DIR, data_dir or DATA_DIR)
        super().__init__(store)
        self.config_path = store.paths["config"]
        self.history_path = store.paths["history"]
        self.playback_path = store.paths["playback"]
