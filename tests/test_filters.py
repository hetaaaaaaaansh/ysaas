import unittest

from ysaas.filters import is_short


class IsShortTests(unittest.TestCase):
    def test_vertical_short(self):
        self.assertTrue(is_short({"duration": 30, "width": 1080, "height": 1920}))

    def test_too_long(self):
        self.assertFalse(is_short({"duration": 181, "width": 1080, "height": 1920}))
        self.assertTrue(is_short({"duration": 181, "width": 1080, "height": 1920}, 300))

    def test_horizontal(self):
        self.assertFalse(is_short({"duration": 30, "width": 1920, "height": 1080}))

    def test_null_dimensions_do_not_crash(self):
        # The original code raised TypeError here (None > None).
        self.assertFalse(is_short({"duration": 30, "width": None, "height": None}))
        self.assertFalse(is_short({"duration": None, "width": 1080, "height": 1920}))
        self.assertFalse(is_short({}))

    def test_dimensions_from_formats(self):
        info = {"duration": 20, "formats": [{"width": 360, "height": 640}, {"width": 1080, "height": 1920}, {}]}
        self.assertTrue(is_short(info))


if __name__ == "__main__":
    unittest.main()
