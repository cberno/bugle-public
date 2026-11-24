"""The paper must be entirely defined by bugle.toml."""

import tomllib
import unittest
from pathlib import Path

import bugle_action as bugle

ROOT = Path(__file__).resolve().parent.parent


class ConfigTests(unittest.TestCase):
    def test_config_has_every_required_field(self):
        config = tomllib.loads((ROOT / "bugle.toml").read_text())
        for section, keys in {
            "paper": ("name", "timezone"),
            "location": ("latitude", "longitude", "description"),
            "household": ("description",),
            "local_news": ("feeds",),
        }.items():
            for key in keys:
                self.assertIn(key, config[section], f"{section}.{key}")

    def test_masthead_shrinks_long_names_and_keeps_short_ones_large(self):
        self.assertEqual(bugle.masthead("The Bugle")["size"], 66)
        long = bugle.masthead("The Hendricks-Okonkwo Family Morning Gazette")
        self.assertLess(long["size"], 66)
        self.assertGreaterEqual(long["size"], 34)
        self.assertEqual([w["lead"] for w in long["words"]], ["T", "H", "F", "M", "G"])

    def test_editor_prompt_uses_the_configured_paper(self):
        prompt = bugle.editor_prompt({"high": 50}, None, [], {"local": {"items": [], "failed": []}},
                                     [], None, [])
        self.assertIn(bugle.PAPER_NAME, prompt)
        self.assertIn(bugle.CONFIG["location"]["description"], prompt)
        self.assertIn(bugle.CONFIG["household"]["description"], prompt)

    def test_page_template_has_no_hard_coded_paper_name(self):
        html = (ROOT / "trmnl_template.html").read_text()
        self.assertNotIn("Bugle", html.replace("{{", "").split("<body>")[1])

    def test_page_template_reads_the_local_desk(self):
        self.assertIn("headlines.local", (ROOT / "trmnl_template.html").read_text())

    def test_volume_never_goes_below_one(self):
        self.assertGreaterEqual(bugle.VOLUME, 1)


if __name__ == "__main__":
    unittest.main()
