# SPDX-License-Identifier: GPL-2.0
"""Card pictures decoded into a GdkPixbuf without gdk-pixbuf's loaders.

    decode(path, (w, h)) -> a Pixbuf fitting w x h (its shape kept), or None

gdk-pixbuf 2.44 on Alpine has no loaders of its own: every JPEG goes to
glycin, a loader process in a bwrap sandbox, and on the tablet one such
loader deadlocked and every GdkPixbuf call in the program waited for it,
the GTK thread included. So pictures are decoded with Pillow (JPEG at a
reduced scale already, which is also quicker on the tablet's CPU) and the
pixels handed to GdkPixbuf as bytes, which needs no loader. Without Pillow
gdk-pixbuf decodes them, one at a time. Pictures over max_pixels (told
before decoding) are refused: they come from the network.
"""
import threading

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

try:
    from PIL import Image
except ImportError:  # gdk-pixbuf then, one picture at a time
    Image = None

MAX_PIXELS = 4 * 1000 * 1000  # cards are 320x180, avatars at most 900x900
_one_at_a_time = threading.Lock()


def decode(path, size, max_pixels=MAX_PIXELS):
    try:
        if Image is not None:
            return _with_pillow(path, size, max_pixels)
        with _one_at_a_time:
            return _with_gdk_pixbuf(path, size, max_pixels)
    except Exception:  # whatever the file holds, a card goes without
        return None


def _with_pillow(path, size, max_pixels):
    with Image.open(path) as im:
        w, h = im.size
        if w <= 0 or h <= 0 or w * h > max_pixels:
            return None
        im.draft("RGB", size)  # JPEG: decoded at 1/2, 1/4 or 1/8 when that is enough
        im = im.convert("RGB")
        # Fitted to the box both ways, as gdk-pixbuf's at_scale did: a
        # 320x180 picture fills a 352x198 card.
        k = min(size[0] / im.width, size[1] / im.height)
        w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
        if (w, h) != im.size:
            im = im.resize((w, h), Image.BILINEAR)
        return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(im.tobytes()),
                                               GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)


def _with_gdk_pixbuf(path, size, max_pixels):
    _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
    if w <= 0 or h <= 0 or w * h > max_pixels:
        return None
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size[0], size[1], True)
