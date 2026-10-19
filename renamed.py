# SPDX-License-Identifier: GPL-2.0
"""Видео was b8k-video until 2.0 (ADR 0004): what a person has from then
moves to the new places once, at start, before anything opens it.

    move_old()   -> the paths moved

The data and cache folders (b8k-video) become haltia-video, by haltia-kit;
the files in the folder Видео shared with the other programs
(~/.config/b8k: followed channels and settings, the accounts) go into its
own, ~/.config/haltia-video. Either way a link is left at the old place,
so 1.x, if put back, finds the same files; a file put at the old place
later (the YouTube cookies, by hand) moves at the next start. Nothing is
moved over what is already at the new place. Until 3.0.
"""
import os

from haltia_kit import paths

OLD, NEW = "b8k-video", "haltia-video"
CONFIG_FILES = ("video.json", "video-vk.json", "video-youtube.json", "youtube-cookies.txt")


def move_old(home=None):
    home = home or os.path.expanduser("~")
    config = os.path.join(home, ".config")
    moved = paths.migrate_dirs(OLD, NEW, [config, os.path.join(home, ".local", "share"),
                                          os.path.join(home, ".cache")])
    old, new = os.path.join(config, "b8k"), os.path.join(config, NEW)
    for name in CONFIG_FILES:
        src, dst = os.path.join(old, name), os.path.join(new, name)
        try:
            if os.path.isfile(src) and not os.path.islink(src) and not os.path.lexists(dst):
                os.makedirs(new, 0o700, exist_ok=True)  # accounts are in it
                os.replace(src, dst)
                os.symlink(os.path.join("..", NEW, name), src)
                moved.append(dst)
        except OSError:  # left where it is: read from there no more, but kept
            pass
    return moved
