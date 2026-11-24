"""Port of BugleCore's GradeEngineTests.swift — the Python engine must agree."""

import unittest

import grade_engine as ge
from grade_engine import Alert, Day, Hour, Preferences


def make_day(high, low, temp=None, humidity=45, dew=50, sun=0.75, wind=5, gust=10,
             wet_hours=0, snowfall_in=0):
    base = temp if temp is not None else min(high, 72)
    hours = []
    for hour in range(8, 20):
        wet = hour < 8 + wet_hours
        hours.append(Hour(hour=hour, temperature_f=base, relative_humidity=humidity,
                          dew_point_f=dew, precipitation_probability=70 if wet else 5,
                          precipitation_in=0.05 if wet else 0, snowfall_in=0,
                          weather_code=61 if wet else 0, wind_speed_mph=wind,
                          wind_gust_mph=gust))
    return Day(date_key="2026-09-06", high_f=high, low_f=low, hourly=hours,
               precipitation_sum_in=wet_hours * 0.05, snowfall_sum_in=snowfall_in,
               sunshine_duration_s=43_200 * sun, daylight_duration_s=43_200,
               wind_speed_max_mph=wind)


def grade(day, **kw):
    return ge.grade(day, **kw).grade


def nice_day():
    return make_day(72, 58, dew=50, sun=0.80)


class GradeEngineTests(unittest.TestCase):
    def test_perfect_day_is_a_plus(self):
        self.assertEqual(grade(make_day(72, 58, dew=50, sun=0.80, wind=5, gust=10)), ge.A_PLUS)

    def test_perfect_day_editorial_describes_weather_not_alerts(self):
        report = ge.grade(make_day(72, 58, dew=50, sun=0.80, wind=5, gust=10))
        self.assertEqual(report.grade, ge.A_PLUS)
        self.assertIn("High 72°F", report.editorial)
        self.assertNotIn("alert", report.editorial.lower())

    def test_good_but_warm_day_is_b(self):
        day = make_day(90, 70, temp=84, humidity=30, dew=52, sun=0.75)
        self.assertEqual(grade(day), ge.B)

    def test_hot_day_is_c_plus(self):
        day = make_day(94, 75, temp=92, humidity=30, dew=55, sun=0.75)
        self.assertEqual(grade(day), ge.C_PLUS)

    def test_sticky_day_is_c(self):
        day = make_day(84, 72, temp=80, humidity=70, dew=71, sun=0.70)
        self.assertEqual(grade(day), ge.C)

    def test_brief_rain_is_b(self):
        self.assertEqual(grade(make_day(74, 60, dew=55, sun=0.60, wet_hours=2)), ge.B)

    def test_on_and_off_rain_is_c(self):
        self.assertEqual(grade(make_day(74, 60, dew=55, sun=0.55, wet_hours=5)), ge.C)

    def test_rain_most_of_day_is_d_plus(self):
        self.assertEqual(grade(make_day(74, 60, dew=55, sun=0.30, wet_hours=8)), ge.D_PLUS)

    def test_heat_index_105_is_f(self):
        day = make_day(98, 80, temp=96, humidity=60, dew=75, sun=0.70)
        self.assertEqual(grade(day), ge.F)

    def test_sunny_42_degree_winter_day_is_b_minus(self):
        day = make_day(42, 30, temp=40, humidity=45, dew=25, sun=0.75, wind=4, gust=8)
        self.assertEqual(grade(day), ge.B_MINUS)

    def test_raw_cold_is_c(self):
        day = make_day(42, 34, temp=40, humidity=80, dew=37, sun=0.10, wind=15, gust=25,
                       wet_hours=3)
        self.assertEqual(grade(day), ge.C)

    def test_freezing_rain_is_d_plus(self):
        day = make_day(34, 29, temp=32, humidity=90, dew=31, sun=0.05, wet_hours=1)
        day.hourly[0].weather_code = 66
        self.assertEqual(grade(day), ge.D_PLUS)

    def test_advisory_caps_at_d(self):
        self.assertEqual(grade(nice_day(), alerts=[Alert("Heat Advisory")]), ge.D)

    def test_warning_is_f(self):
        self.assertEqual(grade(nice_day(), alerts=[Alert("Severe Thunderstorm Warning")]), ge.F)

    def test_watch_is_informational_only(self):
        baseline = grade(nice_day())
        report = ge.grade(nice_day(), alerts=[Alert("Severe Thunderstorm Watch")])
        self.assertEqual(report.grade, baseline)
        alerts = next(f for f in report.factors if f.factor == "alerts")
        self.assertEqual(alerts.grade, ge.A_PLUS)

    def test_evening_watch_does_not_change_day_grade(self):
        alert = Alert("Severe Thunderstorm Watch", starts_at="2026-09-06T20:00:00Z",
                      ends_at="2026-09-06T23:00:00Z")
        self.assertEqual(grade(nice_day(), alerts=[alert]), grade(nice_day()))

    def test_short_evening_advisory_does_not_tank_day(self):
        alert = Alert("Heat Advisory", starts_at="2026-09-06T19:00:00Z",
                      ends_at="2026-09-06T20:00:00Z")
        self.assertEqual(grade(nice_day(), alerts=[alert]), grade(nice_day()))

    def test_material_daytime_advisory_caps_at_d(self):
        alert = Alert("Heat Advisory", starts_at="2026-09-06T15:00:00Z",
                      ends_at="2026-09-06T18:00:00Z")
        self.assertEqual(grade(nice_day(), alerts=[alert]), ge.D)

    def test_nighttime_warning_does_not_tank_day(self):
        alert = Alert("Severe Thunderstorm Warning", starts_at="2026-09-06T21:00:00Z",
                      ends_at="2026-09-06T22:00:00Z")
        self.assertEqual(grade(nice_day(), alerts=[alert]), grade(nice_day()))

    def test_daytime_warning_is_f(self):
        alert = Alert("Severe Thunderstorm Warning", starts_at="2026-09-06T18:30:00Z",
                      ends_at="2026-09-06T19:15:00Z")
        self.assertEqual(grade(nice_day(), alerts=[alert]), ge.F)

    def test_statement_does_not_change_grade_even_if_moderate(self):
        baseline = grade(nice_day())
        report = ge.grade(nice_day(), alerts=[Alert("Beach Hazards Statement",
                                                    severity="Moderate")])
        self.assertEqual(report.grade, baseline)
        alerts = next(f for f in report.factors if f.factor == "alerts")
        self.assertEqual(alerts.grade, ge.A_PLUS)

    def test_warm_preference_can_improve_warm_dry_day(self):
        day = make_day(90, 70, temp=84, humidity=30, dew=52, sun=0.75)
        self.assertGreater(grade(day, preferences=Preferences(temperature_bias=1)), grade(day))

    def test_humidity_tolerance_can_improve_muggy_day(self):
        day = make_day(82, 72, temp=80, humidity=68, dew=70, sun=0.75)
        self.assertGreater(grade(day, preferences=Preferences(humidity_tolerance=2)),
                           grade(day))

    # --- Bugle-side additions ---
    def test_display_uses_true_minus_sign(self):
        self.assertEqual(ge.display(ge.B_MINUS), "B−")
        self.assertEqual(ge.display(ge.F), "F")

    def test_open_meteo_adapter_builds_day(self):
        payload = {
            "timezone": "America/New_York",
            "hourly": {
                "time": [f"2026-11-24T{h:02d}:00" for h in range(24)],
                "temperature_2m": [50] * 24, "relative_humidity_2m": [60] * 24,
                "dew_point_2m": [38] * 24, "precipitation_probability": [10] * 24,
                "precipitation": [0] * 24, "snowfall": [0] * 24,
                "weather_code": [3] * 24, "wind_speed_10m": [6] * 24,
                "wind_gusts_10m": [12] * 24,
            },
            "daily": {
                "time": ["2026-11-24"], "temperature_2m_max": [55],
                "temperature_2m_min": [40], "precipitation_sum": [0],
                "snowfall_sum": [0], "sunshine_duration": [20000],
                "daylight_duration": [35000], "wind_speed_10m_max": [8],
            },
        }
        day = ge.day_from_open_meteo(payload)
        self.assertEqual(len(day.usable_hours), 12)
        report = ge.grade(day)
        self.assertEqual(report.grade, ge.A)  # 55°F high is in the ideal band

    def test_nws_adapter_reads_timing(self):
        alerts = ge.alerts_from_nws({"features": [{"properties": {
            "event": "Wind Advisory", "headline": "Wind Advisory issued",
            "onset": "2026-11-24T10:00:00-05:00", "ends": "2026-11-24T18:00:00-05:00",
        }}]})
        self.assertEqual(alerts[0].starts_at, "2026-11-24T10:00:00-05:00")


if __name__ == "__main__":
    unittest.main()
