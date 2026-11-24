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
            "paper": ("name",),
            "location": ("latitude", "longitude", "description", "timezone", "country_code"),
            "weather": ("units",),
            "birds": ("radius_km",),
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


class UnitsTests(unittest.TestCase):
    def test_metric_households_get_celsius_and_kmh(self):
        from unittest.mock import patch
        with patch.object(bugle, "METRIC", True):
            self.assertEqual(bugle.temp(50), 10)
            self.assertEqual(bugle.speed(10), 16)
            self.assertEqual(bugle.localize("High 50°F. Winds to 10 mph, gusts to 20 mph."),
                             "High 10°C. Winds to 16 km/h, gusts to 32 km/h.")
        with patch.object(bugle, "METRIC", False):
            self.assertEqual(bugle.localize("High 50°F."), "High 50°F.")

    def test_weather_preferences_come_from_the_settings(self):
        self.assertIsInstance(bugle.PREFS, bugle.ge.Preferences)

    def test_alerts_are_us_only(self):
        from unittest.mock import patch
        with patch.object(bugle, "COUNTRY", "GB"), \
             patch.object(bugle, "get_json", side_effect=AssertionError("should not call NWS")):
            self.assertEqual(bugle.get_alerts(), [])


class MetricWeatherTests(unittest.TestCase):
    def test_forecast_is_converted_for_the_page_but_graded_in_fahrenheit(self):
        from unittest.mock import patch
        hours = 24
        payload = {
            "timezone": "Europe/London",
            "hourly": {"time": [f"2026-11-24T{h:02d}:00" for h in range(hours)],
                       "temperature_2m": [50] * hours, "apparent_temperature": [48] * hours,
                       "relative_humidity_2m": [70] * hours, "dew_point_2m": [40] * hours,
                       "precipitation_probability": [10] * hours, "precipitation": [0] * hours,
                       "snowfall": [0] * hours, "weather_code": [3] * hours,
                       "cloud_cover": [80] * hours, "wind_speed_10m": [10] * hours,
                       "wind_gusts_10m": [20] * hours},
            "daily": {"time": ["2026-11-24"], "temperature_2m_max": [59],
                      "temperature_2m_min": [41], "precipitation_sum": [0], "snowfall_sum": [0],
                      "precipitation_probability_max": [10], "sunrise": ["2026-11-24T07:35"],
                      "sunset": ["2026-11-24T16:10"], "sunshine_duration": [10000],
                      "daylight_duration": [30000], "wind_speed_10m_max": [12]},
        }
        with patch.object(bugle, "METRIC", True), \
             patch.object(bugle, "get_json", return_value=payload) as get:
            weather = bugle.get_weather()
        self.assertEqual((weather["high"], weather["low"]), (15, 5))
        self.assertEqual(weather["hours"][0]["wind"], 16)
        self.assertEqual(weather["day"].high_f, 59)          # the engine still grades in °F
        self.assertEqual(get.call_args.kwargs["params"]["timezone"], bugle.TZ.key)
