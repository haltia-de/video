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
        pix = pictures.decode(self.picture(480, 360), (352, 198))  # 4:3 in a 16:9 box
        self.assertEqual((pix.get_width(), pix.get_height()), (264, 198))

    def test_the_pixels_are_the_pictures(self):
        pix = pictures.decode(self.picture(320, 180, "PNG"), (352, 198))
        r, g, b = pix.get_pixels()[:3]
        self.assertEqual((r, g, b), (200, 30, 40))
        self.assertFalse(pix.get_has_alpha())

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

    def test_without_pillow_gdk_pixbuf_one_at_a_time(self):
        saved = pictures.Image
        pictures.Image = None
        self.addCleanup(setattr, pictures, "Image", saved)
        pix = pictures.decode(self.picture(640, 360, "PNG"), (352, 198))
        self.assertEqual((pix.get_width(), pix.get_height()), (352, 198))
        self.assertIsNone(pictures.decode(self.picture(3000, 2000, "PNG"), (352, 198)))


if __name__ == "__main__":
    unittest.main()
