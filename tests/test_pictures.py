# SPDX-License-Identifier: GPL-2.0
import os
import tempfile
import unittest

import pictures

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "Pillow makes the test pictures")
class DecodeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def picture(self, w, h, fmt="JPEG", mode="RGB"):
        path = os.path.join(self.dir, "p%dx%d.%s" % (w, h, fmt.lower()))
        Image.new(mode, (w, h), (200, 30, 40) if mode == "RGB" else (200, 30, 40, 128)).save(path, fmt)
        return path

    def test_fits_the_card_keeping_its_shape(self):
        pix = pictures.decode(self.picture(1280, 720), (352, 198))
        self.assertEqual((pix.get_width(), pix.get_height()), (352, 198))
        pix = pictures.decode(self.picture(900, 900), (96, 96))
        self.assertEqual((pix.get_width(), pix.get_height()), (96, 96))
        pix = pictures.decode(self.picture(320, 180), (352, 198))  # YouTube's: made larger
        self.assertEqual((pix.get_width(), pix.get_height()), (352, 198))
        pix = pictures.decode(self.picture(480, 360), (352, 198))  # 4:3: its 16:9 middle
        self.assertEqual((pix.get_width(), pix.get_height()), (352, 198))
        pix = pictures.decode(self.picture(180, 320), (352, 198))  # upright: whole, narrower
        self.assertEqual((pix.get_width(), pix.get_height()), (111, 198))

    def test_four_three_drops_its_black_bars(self):
        path = os.path.join(self.dir, "bars.png")
        im = Image.new("RGB", (480, 360), (0, 0, 0))
        im.paste((200, 30, 40), (0, 45, 480, 315))  # 16:9 between bars
        im.save(path)
        pix = pictures.decode(path, (640, 360))
        data, stride = pix.get_pixels(), pix.get_rowstride()
        for y in (0, 359):
            self.assertEqual(tuple(data[y * stride:y * stride + 3]), (200, 30, 40))

    def test_the_pixels_are_the_pictures(self):
        pix = pictures.decode(self.picture(320, 180, "PNG"), (352, 198))
        r, g, b = pix.get_pixels()[:3]
        self.assertEqual((r, g, b), (200, 30, 40))
        self.assertFalse(pix.get_has_alpha())

    def test_a_surface_for_the_screen_with_the_same_pixels(self):
        surface = pictures.surface(self.picture(1280, 720, "PNG"), (352, 198))
        self.assertEqual((surface.get_width(), surface.get_height()), (352, 198))
        b, g, r = bytes(surface.get_data()[:3])  # cairo's RGB24 on a little-endian machine
        self.assertEqual((r, g, b), (200, 30, 40))
        self.assertEqual(pictures.size_of(surface), 352 * 4 * 198)
        self.assertIsNone(pictures.surface(self.picture(3000, 2000), (352, 198)))

    def test_transparency_is_flattened(self):
        pix = pictures.decode(self.picture(100, 100, "PNG", "RGBA"), (96, 96))
        self.assertFalse(pix.get_has_alpha())

    def test_too_large_or_not_a_picture(self):
        self.assertIsNone(pictures.decode(self.picture(3000, 2000), (352, 198)))
        junk = os.path.join(self.dir, "junk.jpg")
        with open(junk, "wb") as fh:
            fh.write(b"<html>Connection reset</html>")
        self.assertIsNone(pictures.decode(junk, (352, 198)))
        self.assertIsNone(pictures.decode(os.path.join(self.dir, "none.jpg"), (352, 198)))

    def test_only_the_formats_cards_come_in(self):
        self.assertIsNotNone(pictures.decode(self.picture(64, 36, "WEBP"), (352, 198)))
        for fmt in ("BMP", "GIF", "TIFF"):
            self.assertIsNone(pictures.decode(self.picture(64, 36, fmt), (352, 198)), fmt)
        eps = os.path.join(self.dir, "card.jpg")  # what an .eps would start with
        with open(eps, "wb") as fh:
            fh.write(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n")
        self.assertIsNone(pictures.decode(eps, (352, 198)))

    def test_without_pillow_gdk_pixbuf_one_at_a_time(self):
        saved = pictures.Image
        pictures.Image = None
        self.addCleanup(setattr, pictures, "Image", saved)
        pix = pictures.decode(self.picture(640, 360, "PNG"), (352, 198))
        self.assertEqual((pix.get_width(), pix.get_height()), (352, 198))
        self.assertIsNone(pictures.decode(self.picture(3000, 2000, "PNG"), (352, 198)))


class Pix:
    def __init__(self, size):
        self.size = size

    def get_byte_length(self):
        return self.size


class RecentTest(unittest.TestCase):
    def test_kept_by_bytes_the_least_recent_going_first(self):
        r = pictures.Recent(300)
        a, b, c = Pix(100), Pix(100), Pix(100)
        r.put("a", a)
        r.put("b", b)
        r.put("c", c)
        self.assertIs(r.get("a"), a)  # now the most recent
        r.put("d", Pix(100))
        self.assertIsNone(r.get("b"))
        self.assertEqual([r.get(k) is not None for k in "acd"], [True] * 3)
        self.assertEqual(r.bytes, 300)
        r.put("e", Pix(250))  # makes room for itself
        self.assertEqual(list(r.kept), ["e"])
        self.assertEqual(r.bytes, 250)

    def test_put_again_counts_once(self):
        r = pictures.Recent(300)
        r.put("a", Pix(100))
        r.put("a", Pix(120))
        self.assertEqual(r.bytes, 120)

    def test_one_larger_than_all_is_kept_alone(self):
        r = pictures.Recent(100)
        r.put("a", Pix(50))
        big = Pix(500)
        r.put("big", big)
        self.assertIs(r.get("big"), big)
        self.assertIsNone(r.get("a"))

    @unittest.skipIf(Image is None, "Pillow makes the test pictures")
    def test_a_card_is_its_pixels(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "card.jpg")
        Image.new("RGB", (1280, 720)).save(path, "JPEG")
        r = pictures.Recent(1 << 30)
        r.put("card", pictures.decode(path, (352, 198)))
        self.assertEqual(r.bytes, 352 * 198 * 3)


if __name__ == "__main__":
    unittest.main()
