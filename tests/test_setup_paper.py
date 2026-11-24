import datetime as dt
import tomllib
import unittest
from unittest.mock import patch

import setup_paper as sp

TAKOMA = dict(name="Takoma Park", latitude=38.97776, longitude=-77.00748,
              feature_code="PPL", country_code="US", country="United States",
              admin1="Maryland", admin2="Montgomery", timezone="America/New_York",
              population=17000)
TAKOMA_WA = dict(TAKOMA, admin1="Washington", admin2="King", population=50,
                 timezone="America/Los_Angeles", latitude=47.5)
BRISTOL = dict(name="Bristol", latitude=51.45523, longitude=-2.59665, feature_code="PPLA2",
               country_code="GB", country="United Kingdom", admin1="England",
               timezone="Europe/London", population=430000)


def rss(n, days_old=1):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_old)).strftime(
        "%a, %d %b %Y %H:%M:%S +0000")
    items = "".join(f"<item><title>Story {i}</title><pubDate>{when}</pubDate></item>"
                    for i in range(n))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Local</title>{items}</channel></rss>'


class Response:
    def __init__(self, status=200, content=b"", payload=None):
        self.status_code, self.content, self._payload = status, content, payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class PlaceTests(unittest.TestCase):
    def test_state_abbreviation_picks_the_right_town(self):
        self.assertEqual(sp.choose_place([TAKOMA_WA, TAKOMA], "md")["admin1"], "Maryland")
        self.assertEqual(sp.choose_place([TAKOMA, TAKOMA_WA], "washington")["admin1"], "Washington")

    def test_without_a_qualifier_the_bigger_place_wins(self):
        self.assertEqual(sp.choose_place([TAKOMA_WA, TAKOMA], "")["population"], 17000)

    def test_zip_codes_search_the_us(self):
        seen = {}
        def get(url, params=None, **kw):
            seen.update(params)
            return Response(payload={"results": [TAKOMA]})
        with patch.object(sp.requests, "get", side_effect=get):
            place = sp.geocode("20912")
        self.assertEqual(seen["countryCode"], "US")
        self.assertEqual(place["name"], "Takoma Park")

    def test_a_stalled_lookup_is_retried_then_explained(self):
        calls = []
        def get(*a, **kw):
            calls.append(1)
            raise sp.requests.ReadTimeout("slow")
        with patch.object(sp.requests, "get", side_effect=get), patch.object(sp.time, "sleep"):
            with self.assertRaisesRegex(SystemExit, "didn't answer"):
                sp.geocode("Bristol, England")
        self.assertEqual(len(calls), 3)

    def test_unknown_place_explains_itself(self):
        with patch.object(sp.requests, "get", return_value=Response(payload={})):
            with self.assertRaisesRegex(SystemExit, "Couldn't find"):
                sp.geocode("Nowheresville, ZZ")

    def test_description_uses_state_in_us_and_country_abroad(self):
        self.assertEqual(sp.describe(TAKOMA), "Takoma Park, Maryland")
        self.assertEqual(sp.describe(BRISTOL), "Bristol, United Kingdom")


class FeedTests(unittest.TestCase):
    def test_candidates_include_common_feed_paths(self):
        urls = sp.feed_candidates({"site": "https://example.com/news/"})
        self.assertEqual(urls[0], "https://example.com/feed/")

    def test_check_feed_accepts_a_current_feed_and_rejects_a_stale_one(self):
        with patch.object(sp.requests, "get", return_value=Response(content=rss(5).encode())):
            self.assertTrue(sp.check_feed("https://x/feed")["ok"])
        with patch.object(sp.requests, "get", return_value=Response(content=rss(5, 90).encode())):
            self.assertIn("days ago", sp.check_feed("https://x/feed")["reason"])
        with patch.object(sp.requests, "get", return_value=Response(status=404)):
            self.assertFalse(sp.check_feed("https://x/feed")["ok"])

    def test_found_feeds_are_checked_and_local_ones_come_first(self):
        outlets = [{"name": "Dead Blog", "feed": "https://dead.example/feed"},
                   {"name": "Takoma Voice", "feed": "https://voice.example/feed"}]
        def checker(url):
            if "voice" in url or "news.google" in url:
                return {"ok": True, "items": 10, "newest_days": 0}
            return {"ok": False, "reason": "HTTP 404"}
        with patch.object(sp, "ask_claude_for_local_sources",
                          return_value=(["Takoma Park", "Silver Spring"], outlets)):
            feeds, hoods, notes = sp.find_feeds(TAKOMA, "key", checker=checker)
        self.assertEqual(feeds[0]["name"], "Takoma Voice")
        self.assertTrue(any("news.google.com" in f["url"] for f in feeds))
        self.assertTrue(any("Dead Blog" in n for n in notes))
        self.assertEqual(hoods[0], "Takoma Park")

    def test_without_a_key_google_news_is_the_desk(self):
        feeds, hoods, notes = sp.find_feeds(TAKOMA, "", checker=lambda u: {"ok": True, "items": 5})
        self.assertTrue(all("news.google.com" in f["url"] for f in feeds))
        self.assertIn("ANTHROPIC_API_KEY", notes[0])


class BirdTests(unittest.TestCase):
    def test_city_keeps_a_tight_circle_and_country_widens(self):
        self.assertEqual(sp.tune_birds(1, 1, "k", counter=lambda *a: 60)[0], 8)
        counts = {8: 9, 15: 18, 25: 31, 40: 50}
        self.assertEqual(sp.tune_birds(1, 1, "k", counter=lambda la, lo, km, k: counts[km])[0], 25)

    def test_no_key_keeps_the_default(self):
        radius, _, notes = sp.tune_birds(1, 1, "")
        self.assertIsNone(radius)
        self.assertIn("EBIRD_API_KEY", notes[0])


class ConfigTests(unittest.TestCase):
    current = {"paper": {"name": "The Bugle", "edition": "Hometown Edition",
                         "motto": "Be kind.", "launch_date": dt.date(2026, 12, 1)},
               "household": {"description": "two adults and a cat", "voice": "wry"},
               "weather": {"warmth": 2, "rain_tolerance": -1},
               "birds": {"skip": ["European Starling"]},
               "grounds": {"description": "A pear tree."}}

    def build(self, place=TAKOMA, **kw):
        feeds = [dict(name="Takoma Voice", url="https://voice.example/feed")]
        text = sp.build_config(self.current, place, feeds, ["Takoma Park"], 15,
                               today=dt.date(2026, 11, 24), **kw)
        return text, tomllib.loads(text)

    def test_setup_keeps_personal_choices_and_fills_in_location(self):
        _, cfg = self.build()
        self.assertEqual(cfg["paper"]["name"], "The Bugle")
        self.assertEqual(cfg["paper"]["motto"], "Be kind.")
        self.assertEqual(cfg["paper"]["edition"], "Takoma Park Edition")
        self.assertEqual(cfg["paper"]["launch_date"], dt.date(2026, 11, 24))
        self.assertEqual(cfg["household"]["voice"], "wry")
        self.assertEqual(cfg["weather"]["warmth"], 2)
        self.assertEqual(cfg["weather"]["rain_tolerance"], -1)
        self.assertEqual(cfg["birds"]["skip"], ["European Starling"])
        self.assertEqual(cfg["birds"]["radius_km"], 15)
        self.assertEqual(cfg["grounds"]["description"], "A pear tree.")
        self.assertEqual((cfg["location"]["latitude"], cfg["location"]["longitude"]), (38.98, -77.01))
        self.assertEqual(cfg["location"]["timezone"], "America/New_York")
        self.assertEqual(cfg["local_news"]["feeds"], ["https://voice.example/feed"])
        self.assertIn("Takoma Park", cfg["local_news"]["focus"])

    def test_moving_updates_the_edition_unless_it_was_customized(self):
        moved = dict(self.current, paper=dict(self.current["paper"], edition="Missoula Edition"),
                     location={"description": "Missoula, Montana"})
        text = sp.build_config(moved, BRISTOL, [], [], 8, today=dt.date(2026, 11, 24))
        self.assertEqual(tomllib.loads(text)["paper"]["edition"], "Bristol Edition")
        custom = dict(moved, paper=dict(moved["paper"], edition="Porch Edition"))
        text = sp.build_config(custom, BRISTOL, [], [], 8, today=dt.date(2026, 11, 24))
        self.assertEqual(tomllib.loads(text)["paper"]["edition"], "Porch Edition")

    def test_abroad_means_metric(self):
        _, cfg = self.build(place=BRISTOL)
        self.assertEqual(cfg["weather"]["units"], "metric")
        self.assertEqual(cfg["location"]["country_code"], "GB")

    def test_awkward_names_survive_the_round_trip(self):
        _, cfg = self.build(name='The Ørsted "Daily" \\ Bugle', household="Zoë & 🐕")
        self.assertEqual(cfg["paper"]["name"], 'The Ørsted "Daily" \\ Bugle')
        self.assertEqual(cfg["household"]["description"], "Zoë & 🐕")

    def test_dry_run_saves_nothing(self):
        with patch.object(sp, "geocode", return_value=TAKOMA), \
             patch.object(sp, "find_feeds", return_value=([], [], [])), \
             patch.object(sp, "CONFIG_PATH") as path:
            path.read_text.side_effect = FileNotFoundError
            sp.main(["--place", "Takoma Park, MD", "--dry-run"])
        path.write_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
