"""
Bugle Weather grade engine — a faithful Python port of BugleCore's
GradeEngine.swift (cberno/bugle-weather, v0.4).

The engine is a rubric, not a weighted average: each factor gets a grade and
the day inherits the worst meaningful factor. Same input, same grade, every
time. The editorial desk may write the verdict line, but it never sets the
grade.

Scale (worst to best): F, D-, D, D+, C-, C, C+, B-, B, B+, A-, A, A+.
"""

import datetime as dt
import math
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

# ---------------- grades ----------------
F, D_MINUS, D, D_PLUS, C_MINUS, C, C_PLUS, B_MINUS, B, B_PLUS, A_MINUS, A, A_PLUS = range(13)

DISPLAY = {
    A_PLUS: "A+", A: "A", A_MINUS: "A−",
    B_PLUS: "B+", B: "B", B_MINUS: "B−",
    C_PLUS: "C+", C: "C", C_MINUS: "C−",
    D_PLUS: "D+", D: "D", D_MINUS: "D−",
    F: "F",
}

FACTOR_LABELS = {
    "alerts": "Alerts", "heat": "Heat", "cold": "Cold",
    "temperature": "Temperature", "humidity": "Humidity",
    "precipitation": "Precipitation", "conditions": "Conditions",
    "wind": "Wind", "sun": "Sun",
}

PRIORITY = {
    "heat": 0, "cold": 1, "conditions": 2, "precipitation": 3,
    "temperature": 4, "humidity": 5, "wind": 6, "sun": 7, "alerts": 8,
}


def display(grade):
    return DISPLAY[grade]


# ---------------- models ----------------
@dataclass(frozen=True)
class Preferences:
    """-2..+2 each. Defaults reproduce the canonical Bugle rubric."""
    temperature_bias: int = 0
    humidity_tolerance: int = 0
    rain_tolerance: int = 0
    sun_preference: int = 0
    wind_tolerance: int = 0


@dataclass
class Hour:
    hour: int
    temperature_f: float
    relative_humidity: float
    dew_point_f: float
    precipitation_probability: float = 0
    precipitation_in: float = 0
    snowfall_in: float = 0
    weather_code: int = 0
    wind_speed_mph: float = 0
    wind_gust_mph: float = 0


@dataclass
class Day:
    date_key: str
    high_f: float
    low_f: float
    hourly: list
    timezone: str = "UTC"
    precipitation_sum_in: float = 0
    snowfall_sum_in: float = 0
    sunshine_duration_s: float = 28_800
    daylight_duration_s: float = 43_200
    wind_speed_max_mph: float = 5

    @property
    def usable_hours(self):
        return [h for h in self.hourly if 8 <= h.hour < 20]

    @property
    def sunshine_ratio(self):
        if self.daylight_duration_s <= 0:
            return 0.0
        return min(1.0, max(0.0, self.sunshine_duration_s / self.daylight_duration_s))


@dataclass
class Alert:
    event: str
    headline: str = ""
    severity: str = None
    starts_at: str = None   # ISO-8601; None means timing unknown
    ends_at: str = None


@dataclass
class Factor:
    factor: str
    grade: int
    reason: str


@dataclass
class Report:
    date_key: str
    grade: int
    verdict: str
    editorial: str
    high_f: float
    low_f: float
    factors: list = field(default_factory=list)
    primary: Factor = None

    @property
    def display(self):
        return DISPLAY[self.grade]


# ---------------- comfort math ----------------
def heat_index_f(t, rh):
    """NWS Rothfusz regression with standard low/high humidity adjustments."""
    if not (t >= 80 and rh >= 40):
        return t
    simple = 0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094))
    averaged = (simple + t) / 2.0
    if not averaged >= 80:
        return averaged
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
          + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
    if rh < 13 and 80 <= t <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt(max(0, (17 - abs(t - 95)) / 17))
    elif rh > 85 and 80 <= t <= 87:
        hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return hi


def wind_chill_f(t, v):
    """NWS wind chill. Outside its normal domain, returns air temperature."""
    if not (t <= 50 and v > 3):
        return t
    p = v ** 0.16
    return 35.74 + 0.6215 * t - 35.75 * p + 0.4275 * t * p


# ---------------- helpers ----------------
def whole(x):
    """Swift's .rounded(): half away from zero."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def clamp2(v):
    return max(-2, min(2, int(v)))


def is_wet(h):
    return (h.precipitation_probability >= 50 or h.precipitation_in >= 0.02
            or h.snowfall_in >= 0.05)


def parse_iso(raw):
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else None


# ---------------- factors ----------------
def temperature(high, bias):
    shift = clamp2(bias) * 5.0
    ideal_low, ideal_high = 55.0 + shift, 85.0 + shift
    prime_low, prime_high = 65.0 + shift, 78.0 + shift
    if prime_low <= high <= prime_high:
        g = A_PLUS
    elif ideal_low <= high <= ideal_high:
        g = A
    elif high > ideal_high:
        d = high - ideal_high
        g = (A_MINUS if d <= 3 else B if d <= 7 else C_PLUS if d <= 9
             else C if d <= 12 else C_MINUS)
    else:
        d = ideal_low - high
        g = (B_PLUS if d <= 5 else B if d <= 10 else B_MINUS if d <= 15
             else C_PLUS if d <= 20 else C if d <= 23 else C_MINUS if d <= 30
             else D_PLUS if d <= 40 else D)
    return Factor("temperature", g, f"High {whole(high)}°F.")


def sustained_dew(hours):
    points = sorted((h.dew_point_f for h in hours), reverse=True)
    return 50 if not points else points[min(2, len(points) - 1)]


def humidity(hours, tolerance):
    sustained = sustained_dew(hours)
    adjusted = sustained - clamp2(tolerance) * 3.0
    g = (A_PLUS if adjusted < 55 else A if adjusted < 60 else B_PLUS if adjusted < 63
         else B if adjusted < 67 else C_PLUS if adjusted < 70 else C if adjusted < 73
         else C_MINUS)
    return Factor("humidity", g, f"Sustained dew point near {whole(sustained)}°F.")


def precipitation(day, hours, tolerance):
    wet = sum(1 for h in hours if is_wet(h))
    effective = max(0, wet - clamp2(tolerance))
    table = {1: B_PLUS, 2: B, 3: B_MINUS, 4: C_PLUS, 5: C, 6: C_MINUS,
             7: D_PLUS, 8: D_PLUS, 9: D, 10: D}
    if effective == 0:
        g = A_PLUS if day.precipitation_sum_in < 0.02 else A
    else:
        g = table.get(effective, D_MINUS)
    if wet == 0:
        reason = "Dry through the usable day."
    elif wet == 1:
        reason = "One likely wet hour."
    else:
        reason = f"{wet} likely wet hours from 8 AM–8 PM."
    return Factor("precipitation", g, reason)


def sun(ratio, preference):
    pct = ratio * 100
    shift = clamp2(preference) * 7.5
    if pct >= 70 + shift: g = A_PLUS
    elif pct >= 50 + shift: g = A
    elif pct >= 35 + shift: g = A_MINUS
    elif pct >= 20 + shift: g = B_PLUS
    elif pct >= 10 + shift: g = B
    else: g = B_MINUS  # clouds alone never create a C/D/F day
    return Factor("sun", g, f"About {whole(pct)}% of daylight is forecast as sunshine.")


def wind(hours, tolerance):
    max_wind = max((h.wind_speed_mph for h in hours), default=0)
    max_gust = max((h.wind_gust_mph for h in hours), default=0)
    t = clamp2(tolerance)

    def within(w, g):
        return max_wind <= max(1, w + t * 2.5) and max_gust <= max(3, g + t * 4.0)

    for limit, g in (((8, 15), A_PLUS), ((12, 20), A), ((16, 27), B_PLUS),
                     ((20, 34), B), ((24, 39), B_MINUS), ((28, 44), C_PLUS),
                     ((34, 50), C)):
        if within(*limit):
            break
    else:
        g = D_PLUS
    return Factor("wind", g, f"Winds to {whole(max_wind)} mph, gusts to {whole(max_gust)} mph.")


def heat(hours):
    max_hi = max((heat_index_f(h.temperature_f, h.relative_humidity) for h in hours),
                 default=70)
    g = (A_PLUS if max_hi < 95 else C_PLUS if max_hi < 97 else C if max_hi < 100
         else D_PLUS if max_hi < 102 else D if max_hi < 105 else F)
    reason = (f"Peak heat index near {whole(max_hi)}°F." if max_hi >= 95
              else "No meaningful heat-index penalty.")
    return Factor("heat", g, reason)


def cold(hours):
    min_wc = min((wind_chill_f(h.temperature_f, h.wind_speed_mph) for h in hours),
                 default=60)
    g = (A_PLUS if min_wc >= 25 else C_PLUS if min_wc >= 20 else C if min_wc >= 10
         else D_PLUS if min_wc >= 0 else D if min_wc >= -10 else D_MINUS)
    reason = (f"Lowest daytime wind chill near {whole(min_wc)}°F." if min_wc < 25
              else "No meaningful wind-chill penalty.")
    return Factor("cold", g, reason)


def conditions(day, hours):
    codes = [h.weather_code for h in hours]
    freezing = sum(1 for c in codes if c in (56, 57, 66, 67))
    heavy_freezing = 67 in codes
    hail_storm = any(c in (96, 99) for c in codes)
    thunder = codes.count(95)
    fog = sum(1 for c in codes if c in (45, 48))
    snow_hours = sum(1 for c in codes if c in (71, 73, 75, 77, 85, 86))
    wet_hours = sum(1 for h in hours if is_wet(h))
    max_wind = max((h.wind_speed_mph for h in hours), default=day.wind_speed_max_mph)

    candidates = [(A_PLUS, "No disruptive weather conditions.")]
    if freezing > 0:
        candidates.append((D if (heavy_freezing or freezing >= 3) else D_PLUS,
                           "A freezing-rain or freezing-drizzle window is possible."
                           if freezing == 1 else
                           "Freezing precipitation is possible for several hours."))
    if hail_storm:
        candidates.append((D, "Thunderstorms with hail are forecast."))
    elif thunder > 0:
        candidates.append((C_MINUS if thunder >= 3 else C_PLUS,
                           "A thunderstorm window is forecast." if thunder == 1
                           else "Thunderstorms are possible for several hours."))
    if fog > 0:
        candidates.append((C_PLUS if fog >= 3 else B_MINUS,
                           "A foggy hour is possible." if fog == 1
                           else "Fog may linger for several hours."))
    if snow_hours > 0 or day.snowfall_sum_in > 0.05:
        s = day.snowfall_sum_in
        g = ((B_PLUS if snow_hours <= 2 else B) if s < 0.5 else B if s < 1.5
             else C_PLUS if s < 3.5 else C if s < 6.5 else D_PLUS)
        candidates.append((g, f"About {s:.1f} in of snow is forecast."))
    # "Raw cold" from the original Bugle rubric: cold plus wet and/or windy.
    if day.high_f <= 45 and (wet_hours >= 2 or max_wind >= 12):
        raw = C if (wet_hours >= 2 and max_wind >= 12) else C_PLUS
        candidates.append((raw, f"Raw cold: high {whole(day.high_f)}°F with "
                                f"{'wet weather' if wet_hours >= 2 else 'wind'}."))
    worst = min(candidates, key=lambda c: c[0])
    return Factor("conditions", worst[0], worst[1])


def usable_window(day):
    try:
        zone = ZoneInfo(day.timezone)
    except Exception:
        zone = dt.timezone.utc
    try:
        date = dt.date.fromisoformat(day.date_key)
    except ValueError:
        return None
    start = dt.datetime(date.year, date.month, date.day, 8, tzinfo=zone)
    return start, start.replace(hour=20)


def overlap_minutes(alert, day):
    window = usable_window(day)
    if window is None:
        return 0
    start, end = parse_iso(alert.starts_at), parse_iso(alert.ends_at)
    if start is None and end is None:
        return 0
    a_start, a_end = start or window[0], end or window[1]
    if a_end <= a_start:
        return 0
    o_start, o_end = max(a_start, window[0]), min(a_end, window[1])
    if o_end <= o_start:
        return 0
    return (o_end - o_start).total_seconds() / 60


def timing_phrase(alert, minutes):
    if alert.starts_at is None and alert.ends_at is None:
        return ""
    if minutes <= 0:
        return " outside the daytime grading window"
    hours = minutes / 60
    if hours < 1.5:
        return " overlapping about 1 daytime hour"
    return f" overlapping about {whole(hours)} daytime hours"


def alerts_factor(alerts, day):
    """Watch/statement: informational. Advisory: D with 2+ daytime hours.
    Warning: F if it touches the usable day. Untimed: conservative fallback."""
    worst = (A_PLUS, "No grade-changing weather alerts.")
    informational = []
    for alert in alerts:
        event = alert.event.lower()
        minutes = overlap_minutes(alert, day)
        known = parse_iso(alert.starts_at) is not None or parse_iso(alert.ends_at) is not None
        candidate = None
        if "warning" in event:
            candidate = (F if minutes > 0 else None) if known else F
        elif "advisory" in event:
            candidate = (D if minutes >= 120 else None) if known else D
        if candidate is not None and candidate < worst[0]:
            worst = (candidate, alert.event + timing_phrase(alert, minutes) + ".")
        elif alert.event:
            informational.append(alert.event + timing_phrase(alert, minutes))
    if worst[0] == A_PLUS and informational:
        return Factor("alerts", A_PLUS, informational[0] + " (informational).")
    return Factor("alerts", worst[0], worst[1])


# ---------------- verdicts ----------------
def factor_verdict(factor, fallback):
    return {"heat": "HOT", "cold": "BITTER COLD", "humidity": "STICKY",
            "precipitation": "WET", "conditions": "MESSY", "wind": "BLUSTERY",
            "alerts": "WEATHER ALERT"}.get(factor, fallback)


def verdict(grade, primary):
    fixed = {A_PLUS: "CANCEL-PLANS WEATHER", A: "GLORIOUS", A_MINUS: "NEARLY PERFECT",
             B_PLUS: "VERY GOOD", B: "GOOD DAY", B_MINUS: "GOOD, WITH A CATCH",
             D_MINUS: "VERY ROUGH", F: "STAY IN"}
    if grade in fixed:
        return fixed[grade]
    fallback = {C_PLUS: "USABLE", C: "WORK AROUND IT", C_MINUS: "MARGINAL",
                D_PLUS: "ROUGH", D: "HOSTILE"}[grade]
    return factor_verdict(primary, fallback)


def primary_factor(factors, overall):
    tied = [f for f in factors if f.grade == overall]
    if overall < A_PLUS:
        for f in tied:
            if f.factor == "alerts":
                return f
    return min(tied, key=lambda f: PRIORITY[f.factor]) if tied else None


def positive_editorial(day, hours):
    max_wind = max((h.wind_speed_mph for h in hours), default=day.wind_speed_max_mph)
    return (f"High {whole(day.high_f)}°F, dry, dew point near {whole(sustained_dew(hours))}°F, "
            f"winds to {whole(max_wind)} mph, with about {whole(day.sunshine_ratio * 100)}% sunshine.")


# ---------------- the engine ----------------
def grade(day, preferences=Preferences(), alerts=()):
    hours = day.usable_hours or day.hourly
    factors = [
        alerts_factor(list(alerts), day),
        heat(hours),
        cold(hours),
        temperature(day.high_f, preferences.temperature_bias),
        humidity(hours, preferences.humidity_tolerance),
        precipitation(day, hours, preferences.rain_tolerance),
        conditions(day, hours),
        wind(hours, preferences.wind_tolerance),
        sun(day.sunshine_ratio, preferences.sun_preference),
    ]
    overall = min(f.grade for f in factors)
    # A+ is scarce: every factor must itself be A+.
    if overall == A_PLUS and not all(f.grade == A_PLUS for f in factors):
        overall = A
    primary = primary_factor(factors, overall)
    editorial = (positive_editorial(day, hours) if overall == A_PLUS
                 else (primary.reason if primary else "A broadly usable day."))
    return Report(date_key=day.date_key, grade=overall,
                  verdict=verdict(overall, primary.factor if primary else None),
                  editorial=editorial, high_f=day.high_f, low_f=day.low_f,
                  factors=factors, primary=primary)


# ---------------- Open-Meteo / NWS adapters ----------------
OPEN_METEO_HOURLY = ("temperature_2m,apparent_temperature,relative_humidity_2m,dew_point_2m,"
                     "precipitation_probability,precipitation,snowfall,weather_code,"
                     "cloud_cover,wind_speed_10m,wind_gusts_10m")
OPEN_METEO_DAILY = ("temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,"
                    "precipitation_probability_max,sunrise,sunset,sunshine_duration,"
                    "daylight_duration,wind_speed_10m_max")


def day_from_open_meteo(payload):
    """Build today's Day from an Open-Meteo forecast_days=1 response."""
    h, d = payload["hourly"], payload["daily"]

    def at(block, key, i, default=0):
        values = block.get(key) or []
        value = values[i] if i < len(values) else None
        return default if value is None else value

    hours = []
    for i, stamp in enumerate(h["time"]):
        hours.append(Hour(
            hour=int(stamp[11:13]),
            temperature_f=at(h, "temperature_2m", i),
            relative_humidity=at(h, "relative_humidity_2m", i),
            dew_point_f=at(h, "dew_point_2m", i),
            precipitation_probability=at(h, "precipitation_probability", i),
            precipitation_in=at(h, "precipitation", i),
            snowfall_in=at(h, "snowfall", i),
            weather_code=int(at(h, "weather_code", i)),
            wind_speed_mph=at(h, "wind_speed_10m", i),
            wind_gust_mph=at(h, "wind_gusts_10m", i),
        ))
    return Day(
        date_key=d["time"][0],
        timezone=payload.get("timezone", "UTC"),
        high_f=at(d, "temperature_2m_max", 0),
        low_f=at(d, "temperature_2m_min", 0),
        hourly=hours,
        precipitation_sum_in=at(d, "precipitation_sum", 0),
        snowfall_sum_in=at(d, "snowfall_sum", 0),
        sunshine_duration_s=at(d, "sunshine_duration", 0),
        daylight_duration_s=at(d, "daylight_duration", 0, default=1),
        wind_speed_max_mph=at(d, "wind_speed_10m_max", 0),
    )


def alerts_from_nws(payload):
    out = []
    for feature in payload.get("features", []):
        p = feature.get("properties", {})
        out.append(Alert(event=p.get("event", ""), headline=p.get("headline") or "",
                         severity=p.get("severity"),
                         starts_at=p.get("onset") or p.get("effective"),
                         ends_at=p.get("ends") or p.get("expires")))
    return out
