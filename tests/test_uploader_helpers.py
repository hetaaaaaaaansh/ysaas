import unittest

from ysaas.uploader import _is_quota_error, make_description, make_title


class _Resp:
    def __init__(self, status):
        self.status = status


class _Err:
    def __init__(self, status, content):
        self.resp = _Resp(status)
        self.content = content


class HelperTests(unittest.TestCase):
    def test_title_gets_hashtag_with_space(self):
        self.assertEqual(make_title("Cool clip"), "Cool clip #Shorts")

    def test_title_not_duplicated(self):
        self.assertEqual(make_title("Cool #shorts"), "Cool #shorts")

    def test_title_truncated_to_100(self):
        t = make_title("x" * 300)
        self.assertEqual(len(t), 100)
        self.assertTrue(t.endswith(" #Shorts"))

    def test_description_credit(self):
        d = make_description("https://youtu.be/abc", "Alpha", True)
        self.assertIn("Alpha", d)
        self.assertIn("https://youtu.be/abc", d)
        self.assertEqual(make_description("u", "c", False), "#Shorts")

    def test_quota_detection(self):
        self.assertTrue(_is_quota_error(_Err(403, b'{"reason": "quotaExceeded"}')))
        self.assertFalse(_is_quota_error(_Err(400, b"uploadLimitExceeded")))
        self.assertFalse(_is_quota_error(_Err(403, b"forbidden")))


if __name__ == "__main__":
    unittest.main()
