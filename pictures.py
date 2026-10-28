# SPDX-License-Identifier: GPL-2.0
"""Card pictures decoded into a GdkPixbuf without gdk-pixbuf's loaders.

    decode(path, (w, h)) -> a Pixbuf fitting w x h (its shape kept; a
    landscape picture fills a landscape box, its middle cut out), or None
    surface(path, (w, h)) -> the same as a cairo surface, what the screen
        draws from (a Pixbuf is turned into one at every first draw: some
        4 ms a 600 px card, all the cards of a page at once); without
        Pillow a Pixbuf still, to be turned on the GTK thread

gdk-pixbuf 2.44 on Alpine has no loaders of its own: every JPEG goes to
glycin, a loader process in a bwrap sandbox, and on the tablet one such
loader deadlocked and every GdkPixbuf call in the program waited for it,
the GTK thread included. So pictures are decoded with Pillow (JPEG at a
reduced scale already, which is also quicker on the tablet's CPU) and the
pixels handed to GdkPixbuf as bytes, which needs no loader. Without Pillow
gdk-pixbuf decodes them, one at a time. They come from the network: only
the formats cards come in are opened (not, say, EPS, which Pillow hands
to Ghostscript), and pictures over max_pixels (told before decoding) are
refused.

    Recent(max_bytes) -> the decoded ones kept in memory, by bytes

A card's picture decoded is some 200 KB (its JPEG is a tenth of that and
stays on disk): kept by count, 300 of them took ~60 MB of the tablet's
1 GB, so they are kept by what they take.
"""
import threading
from collections import OrderedDict

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

# PIL.Image, imported by the first decode (in a worker, not before the
# first window: Pillow takes longer to import than any other module the
# program starts with), or None without Pillow: gdk-pixbuf then, one
# picture at a time.
_NOT_YET = object()
Image = _NOT_YET


def _pillow():
    global Image
    if Image is _NOT_YET:
        try:
            from PIL import Image as image
        except ImportError:
            image = None
        Image = image
    return Image

MAX_PIXELS = 4 * 1000 * 1000  # cards are up to 1280x720, avatars at most 900x900
FORMATS = ("JPEG", "PNG", "WEBP")
_one_at_a_time = threading.Lock()


def decode(path, size, max_pixels=MAX_PIXELS):
    try:
        if _pillow() is not None:
            im = _with_pillow(path, size, max_pixels)
            return im and GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(im.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8, im.width, im.height, im.width * 3)
        with _one_at_a_time:
            return _with_gdk_pixbuf(path, size, max_pixels)
    except Exception:  # whatever the file holds, a card goes without
        return None


def surface(path, size, max_pixels=MAX_PIXELS):
    if _pillow() is None:
        return decode(path, size, max_pixels)
    try:
        im = _with_pillow(path, size, max_pixels)
        if im is None:
            return None
        import cairo
        # Cairo's RGB24 is a 32-bit word per pixel, in memory B, G, R and
        # one unused on little-endian machines (all those it runs on).
        data = bytearray(im.tobytes("raw", "BGRX"))
        return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_RGB24, im.width, im.height, im.width * 4)
    except Exception:
        return None


def size_of(picture):
    """The bytes a Pixbuf or a cairo surface takes."""
    if hasattr(picture, "get_byte_length"):
        return picture.get_byte_length()
    return picture.get_stride() * picture.get_height()


def _with_pillow(path, size, max_pixels):
    with Image.open(path, formats=FORMATS) as im:
        w, h = im.size
        if w <= 0 or h <= 0 or w * h > max_pixels:
            return None
        im.draft("RGB", size)  # JPEG: decoded at 1/2, 1/4 or 1/8 when that is enough
        im = im.convert("RGB")
        box = size[0] / size[1]
        if box > 1 and im.width >= im.height and abs(im.width / im.height - box) > 0.01:
            # A card's 16:9 box filled by a landscape picture: its middle
            # (YouTube's 4:3 ones are 16:9 between black bars). An upright
            # one is shown whole, narrower.
            if im.width / im.height > box:
                w = round(im.height * box)
                im = im.crop(((im.width - w) // 2, 0, (im.width + w) // 2, im.height))
            else:
                h = round(im.width / box)
                im = im.crop((0, (im.height - h) // 2, im.width, (im.height + h) // 2))
        # Fitted to the box both ways, as gdk-pixbuf's at_scale did.
        k = min(size[0] / im.width, size[1] / im.height)
        w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
        if (w, h) != im.size:
            im = im.resize((w, h), Image.BILINEAR)
        return im


class Recent:
    """Pictures (Pixbufs or cairo surfaces) by key, the least recently
    used going first once they take more than max_bytes. Not locked: the
    caller holds its own lock."""

    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self.bytes = 0
        self.kept = OrderedDict()  # key: (pixbuf, its bytes), least recent first

    def get(self, key):
        got = self.kept.get(key)
        if got is None:
            return None
        self.kept.move_to_end(key)
        return got[0]

    def put(self, key, pix):
        old = self.kept.pop(key, None)
        if old is not None:
            self.bytes -= old[1]
        size = size_of(pix)
        self.kept[key] = (pix, size)
        self.bytes += size
        while self.bytes > self.max_bytes and len(self.kept) > 1:
            self.bytes -= self.kept.popitem(last=False)[1][1]


def _with_gdk_pixbuf(path, size, max_pixels):
    _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
    if w <= 0 or h <= 0 or w * h > max_pixels:
        return None
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size[0], size[1], True)
