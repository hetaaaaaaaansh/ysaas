import json
import unittest
from pathlib import Path

from ysaas.config import ConfigError, parse_settings, load_settings
from helpers import GOOD_ID, make_settings, tmpdir


class ConfigTests(unittest.TestCase):
    def test_defaults_and_relative_paths(self):
        with tmpdir() as t:
            s = make_settings(Path(t))
            self.assertEqual(s.root, Path(t))
            self.assertEqual(s.download_folder, Path(t) / "downloads")
            self.assertEqual(s.db_path, Path(t) / "data" / "ysaas.db")
            self.assertEqual(s.upload_privacy, "private")
            self.assertEqual(s.token_path, Path(t) / "config" / "tokens" / "main.json")

    def test_bad_channel_id(self):
        with tmpdir() as t:
            with self.assertRaises(ConfigError):
                make_settings(Path(t), channels=[{"name": "x", "channel_id": "nope"}])

    def test_duplicate_channel_names(self):
        with tmpdir() as t:
            chans = [{"name": "A", "channel_id": GOOD_ID}, {"name": "a", "channel_id": GOOD_ID}]
            with self.assertRaises(ConfigError):
                make_settings(Path(t), channels=chans)

    def test_bad_values(self):
        with tmpdir() as t:
            for bad in (
                {"upload_privacy": "everyone"},
                {"max_downloads_per_day": 0},
                {"max_downloads_per_day": True},
                {"credit_sources": "yes"},
                {"target_channel": "../evil"},
            ):
                with self.assertRaises(ConfigError, msg=str(bad)):
                    make_settings(Path(t), **bad)

    def test_missing_and_invalid_file(self):
        with tmpdir() as t:
            with self.assertRaises(ConfigError):
                load_settings(Path(t) / "config" / "settings.json")
            p = Path(t) / "config"
            p.mkdir()
            (p / "settings.json").write_text("{not json")
            with self.assertRaises(ConfigError):
                load_settings(p / "settings.json")

    def test_example_file_is_valid(self):
        example = Path(__file__).resolve().parent.parent / "config" / "settings.example.json"
        parse_settings(json.loads(example.read_text()), example)


if __name__ == "__main__":
    unittest.main()
