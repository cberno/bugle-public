#!/usr/bin/env python3
"""
The press: a one-page household newspaper for an e-ink wall display.
Runs on GitHub's servers every morning; see SETUP.md.

Pipeline: fetch real data -> validated editorial desk -> copy desk checks ->
render a 1404x1872 grayscale PNG -> publish only the image to the display feed.

Your paper's name, place, and voice live in bugle.toml, not here.

Secrets (Settings > Secrets and variables > Actions):
  ANTHROPIC_API_KEY        required — the editorial desk and the copy desk
  EBIRD_API_KEY            recommended — free key from ebird.org/api/keygen;
                           powers Bird of the Day (falls back to a text
                           Grounds Report without it)
  OPENAI_API_KEY           optional — backup desk if Claude is unavailable
  BUGLE_ICS_URL            optional — a Google Calendar secret .ics address;
                           events with "birthday" in the title print
  BUGLE_BIRTHDAYS_ICS_URL  optional — the Birthdays calendar's secret .ics

The weather grade comes from grade_engine.py, a deterministic rubric. The
editorial desk writes the verdict line but never the grade.
"""

import json, os, re, subprocess, sys, tempfile, time, tomllib, datetime as dt
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from pathlib import Path

import requests, feedparser
from icalendar import Calendar
from jinja2 import Environment, FileSystemLoader

import bird_desk
import copy_desk
import grade_engine as ge

HERE = Path(__file__).parent
BIRD_PHOTO = Path(tempfile.gettempdir()) / "bugle_bird_of_the_day.png"
BIRD_PHOTO_SIZE = (634, 641)   # the template's photo frame on a no-birthday day
HISTORY_PATH = HERE / "editorial_history.json"

# Everything personal lives in bugle.toml.
CONFIG = tomllib.loads((HERE / "bugle.toml").read_text(encoding="utf-8"))
PAPER = CONFIG["paper"]
PAPER_NAME = PAPER["name"].strip()
TZ = ZoneInfo(PAPER.get("timezone", "America/New_York"))
LAT = float(CONFIG["location"]["latitude"])
LON = float(CONFIG["location"]["longitude"])
LAUNCH = PAPER.get("launch_date") or dt.date.today()   # Vol. I
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
COPY_DESK_MODEL = os.environ.get("COPY_DESK_MODEL", CLAUDE_MODEL)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ICS_URL = os.environ.get("BUGLE_ICS_URL", "")
BDAY_ICS_URL = os.environ.get("BUGLE_BIRTHDAYS_ICS_URL", "")
EBIRD_KEY = os.environ.get("EBIRD_API_KEY", "")
# Wikipedia and eBird ask automated clients to say who they are.
UA = {"User-Agent": "HouseholdBugle/1.0 (github.com/"
                    f"{os.environ.get('GITHUB_REPOSITORY', 'household-bugle')})"}

# A dead feed is reported in the paper, never papered over.
FEEDS = {"local": list(CONFIG["local_news"]["feeds"])}

# The grounds: what grows at the house, for the rare no-bird day.
MANIFEST = CONFIG.get("grounds", {}).get("description", "").strip()

CURATED_QUOTES = (
    {"text": "Lost time is never found again.", "attr": "Benjamin Franklin, Poor Richard's Almanack"},
    {"text": "It is better to fail in originality than to succeed in imitation.", "attr": "Herman Melville"},
    {"text": "All things excellent are as difficult as they are rare.", "attr": "Baruch Spinoza, Ethics"},
    {"text": "I cannot live without books.", "attr": "Thomas Jefferson, letter to John Adams"},
    {"text": "Forever is composed of nows.", "attr": "Emily Dickinson"},
    {"text": "Life can only be understood backwards; but it must be lived forwards.", "attr": "Søren Kierkegaard, Journals"},
    {"text": "It is better to offer no excuse than a bad one.", "attr": "George Washington"},
    {"text": "There is no charm equal to tenderness of heart.", "attr": "Jane Austen, Emma"},
    {"text": "If men were angels, no government would be necessary.", "attr": "James Madison, Federalist No. 51"},
    {"text": "Nothing great was ever achieved without enthusiasm.", "attr": "Ralph Waldo Emerson, Essays"},
    {"text": "Energy in the executive is a leading character in the definition of good government.", "attr": "Alexander Hamilton, Federalist No. 70"},
    {"text": "There is no exquisite beauty without some strangeness in the proportion.", "attr": "Edgar Allan Poe, Ligeia"},
    {"text": "Great works are performed not by strength but by perseverance.", "attr": "Samuel Johnson, Rasselas"},
    {"text": "And now that you don't have to be perfect, you can be good.", "attr": "John Steinbeck, East of Eden"},
    {"text": "Brevity is the soul of wit.", "attr": "William Shakespeare, Hamlet"},
    {"text": "The heart has its reasons which reason knows nothing of.", "attr": "Blaise Pascal, Pensées"},
    {"text": "I am large, I contain multitudes.", "attr": "Walt Whitman, Song of Myself"},
    {"text": "If there is no struggle, there is no progress.", "attr": "Frederick Douglass"},
)
# Used only when no bird can be featured (eBird or photo desks down).
GROUNDS_BEATS = (
    "one tree or shrub from THE GROUNDS; notice one seasonal change",
    "the lawn or beds and the current weather pattern",
    "one flowering plant from THE GROUNDS and what the season is doing to it",
    "a restrained whole-yard outlook for today's date and weather; no daylight comparison",
)
BIRD_NOTE_MAX_WORDS = 34   # the desk is asked for under 30; the copy desk enforces this
BIRD_NOTE_HARD_LIMIT = 48  # beyond this the layout overflows; always a rewrite
EDITOR_CANDIDATES = 4    # drafts per desk before giving up (or repairing)
WIRE_DOWN = ("Weather wire down",
             "The forecast service did not answer before press time.")
GROUNDS_MAX_WORDS = 40

EDITORIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "quote": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "attr": {"type": "string"}},
            "required": ["text", "attr"],
            "additionalProperties": False,
        },
        "weather": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string"},
                "editorial": {"type": "string"},
            },
            "required": ["verdict", "editorial"],
            "additionalProperties": False,
        },
        "headlines": {
            "type": "object",
            "properties": {
                "local": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "head": {"type": "string"},
                            "line": {"type": "string"},
                        },
                        "required": ["head", "line"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["local"],
            "additionalProperties": False,
        },
        "grounds": {"type": "string"},
    },
    "required": ["quote", "weather", "headlines", "grounds"],
    "additionalProperties": False,
}

NOW = dt.datetime.now(TZ)
DATE_LINE = NOW.strftime("%A, %B %-d, %Y")
VOLUME = max(1, (NOW.date() - LAUNCH).days + 1)

def roman(n):
    vals = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),
            (50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    out = ""
    for v, s in vals:
        while n >= v: out += s; n -= v
    return out


def load_editorial_history():
    try:
        entries = json.loads(HISTORY_PATH.read_text())
        if not isinstance(entries, list):
            raise ValueError("history root must be a list")
        return entries[-21:]
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Editorial history unavailable: {e}")
        return []


def remember_editorial(ed, history, bird=None):
    entry = {
        "date": f"{NOW:%Y-%m-%d}",
        "quote": ed["quote"],
        "bird": bird["common_name"] if bird else "",
        "grounds": ed["grounds"],
        "headlines": ed["headlines"]["local"],
    }
    kept = [item for item in history if item.get("date") != entry["date"]]
    HISTORY_PATH.write_text(json.dumps((kept + [entry])[-21:], indent=2,
                                       ensure_ascii=False) + "\n")


STORY_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "in", "is", "it", "late", "near", "of", "on", "reported", "should",
    "summer", "the", "this", "to", "today", "watch", "week", "with", "will",
}


def story_words(text):
    return {word for word in re.findall(r"[a-z0-9]+", flat(text).lower())
            if len(word) > 2 and word not in STORY_STOP_WORDS}


def same_story(left, right):
    a, b = story_words(left), story_words(right)
    if not a or not b:
        return False
    overlap = len(a & b)
    return overlap >= 2 and overlap / min(len(a), len(b)) >= 0.5


def prepare_headlines(raw, history):
    """Remove stories already printed recently and stale weekend feed entries."""
    recent = []
    for edition in history[-5:]:
        for item in edition.get("headlines", []):
            recent.append(f"{item.get('head', '')} {item.get('line', '')}")

    result = {}
    weekend = NOW.weekday() >= 5
    for desk, payload in raw.items():
        fresh, excluded = [], 0
        for item in payload.get("items", []):
            age = item.get("age_hours")
            stale_weekend = weekend and age is not None and age > 36
            repeated = any(same_story(item.get("title", ""), old) for old in recent)
            if stale_weekend or repeated:
                excluded += 1
                continue
            fresh.append(item)
        result[desk] = {
            "items": fresh,
            "failed": payload.get("failed", []),
            "excluded_as_stale_or_repeated": excluded,
        }
    return result

# ---------------- data ----------------
def get_json(url, attempts=3, timeout=30, **kw):
    """GET with backoff. Open-Meteo occasionally stalls before dawn."""
    last_err = None
    for attempt in range(attempts):
        try:
            r = requests.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            print(f"{urlparse(url).netloc} attempt {attempt + 1} failed: {e}")
            if attempt < attempts - 1:
                time.sleep(5 * 3 ** attempt)
    raise last_err


def clock(stamp):
    """'2026-11-24T06:58' -> '6:58 am'."""
    t = dt.datetime.fromisoformat(stamp)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def get_weather():
    r = get_json("https://api.open-meteo.com/v1/forecast", params=dict(
        latitude=LAT, longitude=LON, timezone="America/New_York",
        hourly=ge.OPEN_METEO_HOURLY, daily=ge.OPEN_METEO_DAILY,
        temperature_unit="fahrenheit", wind_speed_unit="mph",
        precipitation_unit="inch", forecast_days=1))
    h, d = r["hourly"], r["daily"]
    hours = []
    for i, t in enumerate(h["time"]):
        hh = int(t[11:13])
        if hh in (8, 10, 12, 14, 16, 18):
            cc = h["cloud_cover"][i]
            sky = ("clear" if cc < 20 else "mainly clear" if cc < 45
                   else "partly cloudy" if cc < 70 else "overcast")
            hours.append(dict(time=f"{hh:02d}:00",
                              temp=round(h["temperature_2m"][i]),
                              feels=round(h["apparent_temperature"][i]),
                              rain=h["precipitation_probability"][i], sky=sky,
                              wind=round(h["wind_speed_10m"][i])))
    return dict(high=round(d["temperature_2m_max"][0]),
                low=round(d["temperature_2m_min"][0]),
                max_rain=d["precipitation_probability_max"][0],
                sunrise=clock(d["sunrise"][0]), sunset=clock(d["sunset"][0]),
                hours=hours, day=ge.day_from_open_meteo(r))

def get_alerts():
    """Active NWS alerts for the house — the government's words, not vibes."""
    try:
        r = get_json("https://api.weather.gov/alerts/active", attempts=2,
                     params=dict(point=f"{LAT},{LON}"), headers=UA)
        return ge.alerts_from_nws(r)[:3]
    except Exception:
        return []


def grade_weather(weather, alerts):
    """The Bugle Weather engine's verdict, or None when the wire is down."""
    day = weather.get("day")
    if day is None or not day.hourly:
        return None
    return ge.grade(day, alerts=alerts)

def ics_events(url):
    if not url: return []
    try:
        cal = Calendar.from_ical(requests.get(url, timeout=20).content)
    except Exception:
        return []
    today, out = NOW.date(), []
    for c in cal.walk("VEVENT"):
        start = c.get("dtstart").dt
        d = start.date() if isinstance(start, dt.datetime) else start
        if d == today:
            out.append(str(c.get("summary")))
    return out

def get_birthdays():
    names = [e for e in ics_events(BDAY_ICS_URL)]
    names += [e for e in ics_events(ICS_URL) if "birthday" in e.lower()]
    # Dedupe, first names only (privacy: rendered papers are public).
    seen, out = set(), []
    for n in names:
        n = n.replace("'s birthday", "").replace("\u2019s birthday", "").strip()
        first = n.split()[0] if n else n
        if first and first.lower() not in seen:
            seen.add(first.lower()); out.append(first)
    return out

def get_headlines():
    raw = {}
    for desk, urls in FEEDS.items():
        items, failed = [], []
        for url in urls:
            name = urlparse(url).netloc.replace("www.", "")
            try:
                entries = feedparser.parse(url).entries[:8]
                if not entries:
                    failed.append(name); continue
                for e in entries:
                    parsed = (getattr(e, "published_parsed", None)
                              or getattr(e, "updated_parsed", None))
                    published, age_hours = None, None
                    if parsed:
                        published_dt = dt.datetime(*parsed[:6], tzinfo=dt.timezone.utc)
                        published = published_dt.astimezone(TZ).isoformat()
                        age_hours = round(max(0, (NOW - published_dt.astimezone(TZ))
                                              .total_seconds() / 3600), 1)
                    items.append(dict(source=name, title=e.title,
                                      summary=getattr(e, "summary", "")[:200],
                                      published=published, age_hours=age_hours))
            except Exception:
                failed.append(name)
        raw[desk] = dict(items=items, failed=failed)
    return raw

def get_bird(recent_editions):
    """Today's featured bird with its prepared photograph, or None."""
    verify = ((lambda raw, bird: copy_desk.check_photo(raw, bird, claude_json))
              if ANTHROPIC_API_KEY else None)
    return bird_desk.featured_bird(
        EBIRD_KEY, LAT, LON, recent_editions, seed=f"{NOW:%Y-%m-%d}",
        headers=UA, photo_path=BIRD_PHOTO, photo_size=BIRD_PHOTO_SIZE, verify=verify)


def reported_when(obs_dt):
    """eBird '2026-11-22 08:14' -> 'Sunday, Nov. 22' (or 'Today'/'Yesterday')."""
    try:
        day = dt.datetime.strptime(obs_dt[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return ""
    delta = (NOW.date() - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return f"{day:%A}, {day:%b}. {day.day}".replace("May.", "May")

# ---------------- the editor ----------------
def flat(v):
    """The editor sometimes wraps prose in {'text': ...}; unwrap to a string."""
    if isinstance(v, dict):
        v = v.get("text") or next((x for x in v.values()
                                   if isinstance(x, str)), "")
    return str(v or "").strip()

def grade_brief(report):
    """What the editor needs to know about the engine's decision."""
    if report is None:
        return None
    return dict(
        grade=report.display,
        driver=(f"{ge.FACTOR_LABELS[report.primary.factor]}: {report.primary.reason}"
                if report.primary else "No single limiting factor."),
        factors=[f"{ge.FACTOR_LABELS[f.factor]} {ge.display(f.grade)} — {f.reason}"
                 for f in report.factors],
    )


def bird_brief(bird):
    if not bird:
        return None
    return dict(common_name=bird["common_name"], scientific_name=bird["scientific_name"],
                last_reported=reported_when(bird["observed"]), place=bird["place"],
                miles_from_house=bird["miles"],
                reference=bird.get("reference") or bird.get("summary", ""))


def indent(text, pad="  "):
    lines = [l.strip() for l in str(text).strip().splitlines() if l.strip()]
    return ("\n" + pad).join(lines)


def editor_prompt(weather, report, alerts, raw_headlines, birthdays, bird,
                  recent_editions):
    grounds_beat = GROUNDS_BEATS[VOLUME % len(GROUNDS_BEATS)]
    weekend = NOW.weekday() >= 5
    weather_inputs = {k: v for k, v in weather.items() if k != "day"}
    alert_inputs = [dict(event=a.event, headline=a.headline, starts=a.starts_at,
                         ends=a.ends_at) for a in alerts]
    if bird:
        grounds_rule = f"""- grounds: the caption for TODAY'S FEATURED BIRD, printed beside its
  photograph under the bird's name. Two short sentences, under {BIRD_NOTE_MAX_WORDS - 4} words total.
  The name and the report details (when, where, distance) already print in
  their own lines, so do not open with the name and do not repeat the report.
  Say how to recognize it (a field mark, sound, or habit) and one more striking
  fact. Use ONLY facts stated in the bird's reference text — a copy desk checks
  every claim against it and rejects anything else, including comparisons to
  other birds and how common it is here. Expectation language only ("watch
  for", "listen for"); never claim it was seen at the house. No family names."""
    elif not MANIFEST:
        grounds_rule = f"""- grounds: an Almanac note — ONE or two sentences, under 36 words total,
  about what the season and today's weather are doing around
  {CONFIG["location"]["description"]}. Ground every claim in the date and the raw
  weather. Expectation language only ("watch for", "expect"). Make no numerical
  claims beyond the supplied weather numbers. Never chores."""
    else:
        grounds_rule = f"""- grounds: a Grounds Report — ONE or two sentences, under 36 words total,
  focused tightly on TODAY'S GROUNDS BEAT. Do not reuse the main plant or
  observation from the previous four editions unless the raw weather shows a
  meaningful change. Ground every claim in the date, weather, and MANIFEST.
  Expectation language only ("watch for", "should open") — never claim
  something was observed at the house. Make no numerical claims and no
  comparisons to the solstice, a prior month, or facts absent from the inputs.
  If the beat is thin, write one restrained watch sentence. Never chores."""
    return f"""You are the editor of {PAPER_NAME}, a one-screen wall newspaper
for a household in {CONFIG["location"]["description"]}: {CONFIG["household"]["description"]}.
Voice: {CONFIG["household"].get("voice", "dry, warm, terse")}. No exclamation points. REAL facts only — everything you write must be
supported by the raw inputs below or be genuinely well-established fact.

Today is {DATE_LINE}.
WEATHER: {json.dumps(weather_inputs)}
WEATHER GRADE (decided by the Bugle Weather engine; null means the forecast wire is down): {json.dumps(grade_brief(report), ensure_ascii=False)}
ACTIVE NWS ALERTS (official; may be empty): {json.dumps(alert_inputs)}
RAW HEADLINES BY DESK (items tagged by source; 'failed' lists dead feeds): {json.dumps(raw_headlines)}
BIRTHDAYS TODAY: {json.dumps(birthdays)}
TODAY'S FEATURED BIRD (a real eBird report; null means none today): {json.dumps(bird_brief(bird))}
THE GROUNDS at the family's house (may be empty): {json.dumps(MANIFEST)}
RECENT EDITIONS (hard anti-repeat list): {json.dumps(recent_editions[-14:])}
TODAY'S GROUNDS BEAT (used only when there is no featured bird): {grounds_beat}
WEEKEND EDITION: {json.dumps(weekend)}

Return ONLY a JSON object, no markdown fences. The grounds field must be a
PLAIN STRING, never a nested object:
- quote: {{text, attr}} — reserved for the application's curated quotation desk;
  output empty strings for both fields.
- weather: {{verdict, editorial}} — the grade is already printed and is not
  yours to change. verdict is a short punchy line that agrees with that grade
  and its driver; editorial is ONE FACTUAL sentence — numbers, timing,
  official alert wording only — that explains the driver. Never activity
  advice, never name family members or pets, and no holidays, dates, or
  calendar references anywhere in the weather section. A copy desk checks every
  number against the forecast. If NWS alerts exist, use their official facts —
  do not soften or embellish them. If the grade is null, verdict is exactly "Weather wire down" and
  editorial is exactly "The forecast service did not answer before press time."
- headlines: {{local: [{{head, line}} x1-3]}} — bold 2-4 word head + ONE sentence
  under 16 words, rewritten in your own words, from the local items only. Never
  repeat or lightly rewrite any story in RECENT EDITIONS; fewer fresh stories
  are better than recycled copy.
  {indent(CONFIG["local_news"].get("focus", ""))}
  On weekends, use only genuinely fresh items supplied in RAW HEADLINES. If
  fewer than two survive, print the smaller number without padding. If weekend
  items are empty, output exactly: head "Weekend desk", line "Local feeds are
  quiet; no fresh local item cleared the desk." On weekdays, if items were
  excluded as stale or repeated, output head "Desk cleared", line "No fresh,
  unrepeated local item cleared the desk." Use "Wire down" only when feeds
  actually appear in the failed list.
{grounds_rule}
"""


def http_error_detail(response):
    """Return non-sensitive provider error metadata for pressroom diagnostics."""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return ""
    details = []
    for label in ("type", "code"):
        value = error.get(label)
        if value:
            details.append(f"{label}={value}")
    return f" ({', '.join(details)})" if details else ""


def post_with_backoff(provider, url, headers, payload, timeout=150):
    """POST an editor request, retrying only failures that may clear quickly."""
    last_err = None
    started = time.monotonic()
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code not in (408, 409, 429) and r.status_code < 500:
                r.raise_for_status()
            elif r.status_code >= 400:
                raise requests.HTTPError(
                    f"HTTP {r.status_code}{http_error_detail(r)}", response=r
                )
            r.raise_for_status()
            return r.json(), round(time.monotonic() - started, 3)
        except Exception as e:
            last_err = e
            retryable = not isinstance(e, requests.HTTPError) or e.response is None or (
                e.response.status_code in (408, 409, 429) or e.response.status_code >= 500
            )
            print(f"{provider} editor attempt {attempt + 1} failed: {e}")
            if attempt == 2 or not retryable:
                break
            time.sleep(2 ** (attempt + 1))
    raise last_err


def normalize_editorial(ed):
    """Validate both providers against the rendering contract."""
    if not isinstance(ed, dict):
        raise ValueError("editorial response is not an object")
    try:
        quote, weather = ed["quote"], ed["weather"]
        headlines = ed["headlines"]["local"]
        if not isinstance(quote, dict) or not isinstance(weather, dict):
            raise TypeError("quote/weather must be objects")
        if not isinstance(headlines, list) or not 1 <= len(headlines) <= 3:
            raise ValueError("headlines.local must contain one to three items")
        clean_heads = []
        for item in headlines:
            if not isinstance(item, dict):
                raise TypeError("headline must be an object")
            head, line = flat(item["head"]), flat(item["line"])
            if not head or not line:
                raise ValueError("headline text cannot be empty")
            clean_heads.append({"head": head, "line": line})
        clean = {
            "quote": {"text": flat(quote["text"]), "attr": flat(quote["attr"])},
            "weather": {
                "verdict": flat(weather["verdict"]),
                "editorial": flat(weather["editorial"]),
            },
            "headlines": {"local": clean_heads},
            "grounds": flat(ed["grounds"]),
        }
    except (KeyError, TypeError, IndexError) as e:
        raise ValueError(f"editorial response broke the schema: {e}") from e
    if not clean["grounds"] or not clean["weather"]["verdict"]:
        raise ValueError("editorial prose fields cannot be empty")
    return clean


def parse_editor_json(text):
    """Take the first JSON object; editors sometimes add a trailing remark."""
    text = text.strip().removeprefix("```json").removeprefix("```").strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in editor response")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def quote_author_key(author):
    name = flat(author).split(",", 1)[0]
    parts = re.findall(r"[a-z]+", name.lower())
    while parts and parts[-1] in {"jr", "sr", "ii", "iii", "iv"}:
        parts.pop()
    return parts[-1] if parts else ""


def choose_quote(recent_editions):
    used_text = {
        re.sub(r"\W+", " ", flat(old.get("quote", {}).get("text")).lower()).strip()
        for old in recent_editions[-21:]
    }
    recent_authors = {
        quote_author_key(old.get("quote", {}).get("attr"))
        for old in recent_editions[-7:]
    }
    start = VOLUME % len(CURATED_QUOTES)
    ordered = CURATED_QUOTES[start:] + CURATED_QUOTES[:start]
    for quote in ordered:
        text_key = re.sub(r"\W+", " ", quote["text"].lower()).strip()
        if text_key not in used_text and quote_author_key(quote["attr"]) not in recent_authors:
            return dict(quote)
    for quote in ordered:
        text_key = re.sub(r"\W+", " ", quote["text"].lower()).strip()
        if text_key not in used_text:
            return dict(quote)
    return dict(ordered[0])


def validate_novelty(ed, recent_editions, bird_mode=False):
    problems = []
    words = len(ed["grounds"].split())
    if bird_mode:
        if words > BIRD_NOTE_HARD_LIMIT:
            problems.append(f"bird caption runs {words} words; keep it under "
                            f"{BIRD_NOTE_MAX_WORDS - 4}")
    else:
        if words > GROUNDS_MAX_WORDS:
            problems.append(f"Grounds Report runs {words} words; keep it under 36")
        if any(same_story(ed["grounds"], old.get("grounds", ""))
               for old in recent_editions[-4:]):
            problems.append("Grounds Report repeats a recent beat")
        if re.search(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|"
                     r"solstice|midsummer|midwinter|earlier|later|longer|shorter|"
                     r"ago|last month)\b", ed["grounds"].lower()):
            problems.append("Grounds Report contains an unsupported numeric or past-date comparison")
    recent_heads = [item for old in recent_editions[-5:]
                    for item in old.get("headlines", [])]
    for item in ed["headlines"]["local"]:
        candidate = f"{item['head']} {item['line']}"
        if any(same_story(candidate, f"{old.get('head', '')} {old.get('line', '')}")
               for old in recent_heads):
            problems.append(f"local story repeats: {item['head']}")
    if problems:
        raise ValueError("; ".join(problems))
    return ed


def claude_json(content, max_tokens=600):
    """One short call to the copy desk; returns its parsed JSON verdict."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    body, _ = post_with_backoff(
        "Copy desk",
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
         "content-type": "application/json"},
        {"model": COPY_DESK_MODEL, "max_tokens": max_tokens,
         "messages": [{"role": "user", "content": content}]},
        timeout=90,
    )
    text = "".join(b["text"] for b in body["content"] if b["type"] == "text")
    return parse_editor_json(text)


def safe_bird_caption(bird):
    """The reference's own opening sentence: dull, but sourced."""
    text = re.sub(r"\s*\([^)]*\)", "", (bird or {}).get("reference")
                  or (bird or {}).get("summary", "")).strip()
    first = re.split(r"(?<=[.!?])\s+", text)[0] if text else ""
    return shortened(first, BIRD_NOTE_MAX_WORDS - 4) if first else \
        "Reported to eBird near the house this week."


class CopyDesk:
    """Fact-checks the weather lines and bird caption before they print."""

    def __init__(self, weather, report, alerts, bird, ask=None):
        self.report, self.bird = report, bird
        self.ask = ask or claude_json
        self.facts = dict(
            date_line=DATE_LINE,
            weather={k: v for k, v in weather.items() if k != "day"},
            grade=report.display if report else None,
            factors=(grade_brief(report) or {}).get("factors", []),
            alerts=[dict(event=a.event, headline=a.headline, starts=a.starts_at,
                         ends=a.ends_at) for a in alerts],
            bird=dict(common_name=bird["common_name"], scientific_name=bird["scientific_name"],
                      report=dict(last_reported=reported_when(bird["observed"]),
                                  place=bird["place"], miles_from_house=bird["miles"]),
                      reference=bird.get("reference") or bird.get("summary", ""))
            if bird else None,
        )

    def check(self, ed):
        """dict(weather=[...], bird=[...]) or None if the desk is unavailable."""
        try:
            return copy_desk.check_copy(ed, self.facts, self.ask)
        except Exception as e:
            print(f"Copy desk unavailable: {e}")
            return None

    def repair(self, ed, review):
        """Last resort: swap any flagged item for plain, sourced text."""
        fixed = dict(ed, weather=dict(ed["weather"]))
        if review.get("weather"):
            if self.report is None:
                fixed["weather"]["verdict"], fixed["weather"]["editorial"] = WIRE_DOWN
            else:
                fixed["weather"]["verdict"] = self.report.verdict.capitalize()
                fixed["weather"]["editorial"] = self.report.editorial
        if review.get("bird") and self.bird:
            fixed["grounds"] = safe_bird_caption(self.bird)
        return fixed


def run_copy_desk(clean, desk, last_chance):
    """Pass, reject for a rewrite (ValueError), or repair on the last chance."""
    if desk is None:
        return clean, None
    review = desk.check(clean)
    if review is None:
        review = {"weather": [], "bird": [], "unavailable": True}
    words = len(clean["grounds"].split())
    if desk.bird and words > BIRD_NOTE_MAX_WORDS:
        review["bird"] = review["bird"] + [
            f"caption runs {words} words; keep it under {BIRD_NOTE_MAX_WORDS - 4}"]
    if review.get("unavailable") and not review["bird"]:
        return clean, {"status": "unavailable"}
    flagged = review["weather"] + review["bird"]
    if not flagged:
        return clean, {"status": "passed"}
    if not last_chance:
        raise ValueError("copy desk flagged: " + " | ".join(flagged))
    repaired = [k for k in ("weather", "bird") if review[k]]
    print(f"Copy desk replaced {', '.join(repaired)} copy with sourced text: {flagged}")
    return desk.repair(clean, review), {"status": "repaired", "replaced": repaired,
                                        "problems": flagged}


def rejection_note(e):
    if str(e).startswith("copy desk flagged"):
        return ("\nThe copy desk rejected your previous candidate: "
                f"{e}. Fix exactly those claims using only facts in the sources; "
                "keep everything else.")
    return ("\nYour previous candidate was rejected for repetition or schema "
            f"failure: {e}. Produce a genuinely different candidate.")


def ask_claude(prompt, recent_editions=(), bird_mode=False, desk=None):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    correction = ""
    for candidate_number in range(EDITOR_CANDIDATES):
        body, latency = post_with_backoff(
            "Claude",
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
             "content-type": "application/json"},
            {"model": CLAUDE_MODEL, "max_tokens": 1600,
             "messages": [{"role": "user", "content": prompt + correction}]},
        )
        try:
            text = "".join(b["text"] for b in body["content"] if b["type"] == "text")
            result = parse_editor_json(text)
            clean = validate_novelty(normalize_editorial(result), recent_editions, bird_mode)
            clean, review = run_copy_desk(clean, desk,
                                          candidate_number == EDITOR_CANDIDATES - 1)
            return clean, {
                "provider": "anthropic", "model": CLAUDE_MODEL,
                "latency_seconds": latency, "usage": body.get("usage", {}),
                "candidate_number": candidate_number + 1, "copy_desk": review,
            }
        except (ValueError, KeyError, TypeError) as e:
            print(f"Claude candidate {candidate_number + 1} rejected: {e}")
            correction = rejection_note(e)
            last_err = e
    raise last_err


def openai_output_text(body):
    chunks = []
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    if not chunks:
        raise ValueError(f"OpenAI response contained no output text (status={body.get('status')})")
    return "".join(chunks)


def ask_openai(prompt, recent_editions=(), bird_mode=False, desk=None):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    correction = ""
    for candidate_number in range(EDITOR_CANDIDATES):
        body, latency = post_with_backoff(
            "OpenAI",
            "https://api.openai.com/v1/responses",
            {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            {
                "model": OPENAI_MODEL,
                "input": [{"role": "user", "content": prompt + correction}],
                "reasoning": {"effort": "low"},
                "text": {"format": {"type": "json_schema", "name": "bugle_editorial",
                                    "strict": True, "schema": EDITORIAL_SCHEMA}},
                "max_output_tokens": 3500,
                "store": False,
            },
        )
        try:
            result = validate_novelty(
                normalize_editorial(json.loads(openai_output_text(body))), recent_editions,
                bird_mode,
            )
            result, review = run_copy_desk(result, desk,
                                           candidate_number == EDITOR_CANDIDATES - 1)
            usage = body.get("usage", {})
            estimated_cost = None
            if "input_tokens" in usage and "output_tokens" in usage:
                estimated_cost = round(
                    usage["input_tokens"] * 0.20 / 1_000_000
                    + usage["output_tokens"] * 1.20 / 1_000_000, 6
                )
            return result, {
                "provider": "openai", "model": OPENAI_MODEL,
                "latency_seconds": latency, "usage": usage,
                "estimated_cost_usd": estimated_cost,
                "candidate_number": candidate_number + 1, "copy_desk": review,
            }
        except (ValueError, KeyError, TypeError) as e:
            print(f"OpenAI candidate {candidate_number + 1} rejected: {e}")
            correction = rejection_note(e)
            last_err = e
    raise last_err


def shortened(text, words):
    bits = flat(text).replace("\n", " ").split()
    return " ".join(bits[:words]) + ("…" if len(bits) > words else "")


def emergency_editorial(report, raw_headlines, bird):
    """A factual, deterministic edition when both AI desks are unavailable."""
    if report is None:
        verdict, weather_line = WIRE_DOWN
    else:
        verdict, weather_line = report.verdict.capitalize(), report.editorial

    items = raw_headlines.get("local", {}).get("items", [])[:3]
    heads = [{"head": "Morning wire", "line": shortened(i.get("title", ""), 15)}
             for i in items if i.get("title")]
    if not heads:
        failed = raw_headlines.get("local", {}).get("failed", [])
        line = "No fresh local feed item reached the press."
        if failed:
            line = f"No items arrived from {', '.join(failed[:3])}."
        heads = [{"head": "Wire down", "line": line}]

    if bird:
        grounds = safe_bird_caption(bird)  # sourced; report details print below it
    else:
        seasons = {12: "winter", 1: "winter", 2: "winter", 3: "early spring",
                   4: "spring", 5: "late spring", 6: "early summer", 7: "summer",
                   8: "late summer", 9: "early fall", 10: "fall", 11: "late fall"}
        grounds = f"The beds are moving through {seasons[NOW.month]}."
    return normalize_editorial({
        "quote": {"text": "Lost time is never found again.", "attr": "Benjamin Franklin"},
        "weather": {"verdict": verdict, "editorial": weather_line},
        "headlines": {"local": heads},
        "grounds": grounds,
    })


def safe_fetch(name, fetcher, default):
    try:
        return fetcher()
    except Exception as e:
        print(f"{name} desk failed: {e}")
        return default


def write_evaluation(delivered_provider, outputs, diagnostics):
    path = HERE / "evaluations" / f"editorial_{NOW:%Y-%m-%d}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "date": DATE_LINE,
        "delivered_provider": delivered_provider,
        "diagnostics": diagnostics,
        "outputs": outputs,
    }, indent=2, ensure_ascii=False) + "\n")


def masthead(name):
    """Words with an enlarged first letter, sized so the name fits one line."""
    # Playfair caps with the masthead's letter-spacing run ~0.8 em per character.
    size = max(34, min(66, int(1240 / (0.8 * max(len(name), 1)))))
    words = [dict(lead=w[0], rest=w[1:]) for w in name.split()]
    return dict(words=words, size=size, lead_size=round(size * 80 / 66))


def bird_for_page(bird):
    if not bird:
        return None
    when = reported_when(bird["observed"])
    meta = [f"Reported {when.lower() if when in ('Today', 'Yesterday') else when}".strip()]
    if bird["place"]:
        meta.append(bird["place"])
    meta.append(f"{bird['miles']:g} mi away")
    return dict(common_name=bird["common_name"], scientific_name=bird["scientific_name"],
                photo=bird["photo"], credit=bird["credit"], meta=" · ".join(meta))


def render_editorial(ed, base_weather, report, birthdays, bird, district_label,
                     output_stem):
    """Render one validated desk output without mutating the shared source data."""
    from weasyprint import HTML

    rendered_weather = {k: v for k, v in base_weather.items() if k != "day"}
    rendered_weather.update(ed["weather"])
    if report is None:
        rendered_weather.update(grade="\u2014", verdict=WIRE_DOWN[0], editorial=WIRE_DOWN[1])
    else:
        rendered_weather["grade"] = report.display
    data = dict(paper_name=PAPER_NAME, masthead=masthead(PAPER_NAME),
                edition=PAPER.get("edition", ""), motto=PAPER.get("motto", ""),
                date_line=DATE_LINE, volume=roman(VOLUME), quote=ed["quote"],
                weather=rendered_weather, birthdays=birthdays,
                headlines=ed["headlines"], grounds=ed["grounds"],
                bird=bird_for_page(bird), district_label=district_label,
                grounds_label="Grounds Report" if MANIFEST else "Almanac")

    env = Environment(loader=FileSystemLoader(str(HERE)))
    html = env.get_template("trmnl_template.html").render(**data)
    pdf_path = HERE / f"{output_stem}.pdf"
    png_path = HERE / f"{output_stem}.png"
    HTML(string=html, base_url=str(HERE)).write_pdf(str(pdf_path))
    subprocess.run(["pdftoppm", "-png", "-r", "96", "-gray", "-singlefile",
                    pdf_path.name, output_stem], check=True, cwd=HERE)

    dims = subprocess.run(["identify", "-format", "%wx%h", png_path.name],
                          capture_output=True, text=True, cwd=HERE).stdout
    assert dims == "1404x1872", f"Render broke the contract: {dims}"
    pdf_path.unlink()
    return dims

# ---------------- press ----------------
def main():
    weather = safe_fetch("weather", get_weather, {
        "high": "\u2014", "low": "\u2014", "max_rain": "\u2014", "sunrise": "\u2014",
        "sunset": "\u2014", "hours": [],
    })
    alerts = safe_fetch("alerts", get_alerts, [])
    report = grade_weather(weather, alerts)
    raw_headlines = safe_fetch("headlines", get_headlines, {
        "local": {"items": [], "failed": ["all local feeds"]},
    })
    recent_editions = load_editorial_history()
    raw_headlines = prepare_headlines(raw_headlines, recent_editions)
    birthdays = safe_fetch("birthdays", get_birthdays, [])
    bird = safe_fetch("bird", lambda: get_bird(recent_editions), None)
    bird_mode = bird is not None
    prompt = editor_prompt(weather, report, alerts, raw_headlines, birthdays, bird,
                           recent_editions)

    outputs = {}
    diagnostics = {"weather_engine": grade_brief(report),
                   "bird": ({k: v for k, v in bird.items()
                             if k not in ("summary", "reference", "photo")}
                            if bird else None)}
    desk = CopyDesk(weather, report, alerts, bird) if ANTHROPIC_API_KEY else None
    delivered_provider = "anthropic"
    try:
        ed, diagnostics["anthropic"] = ask_claude(prompt, recent_editions, bird_mode, desk)
        outputs["anthropic"] = ed
    except Exception as e:
        diagnostics["anthropic"] = {"error": str(e)}
        print(f"Claude desk unavailable: {e}")
        ed = None

    # During evaluation, OpenAI is a shadow desk. If Claude is down, the same
    # validated output becomes the live failover instead of missing the paper.
    if OPENAI_API_KEY:
        try:
            # The shadow desk is fact-checked only when it is about to print.
            openai_ed, diagnostics["openai"] = ask_openai(prompt, recent_editions, bird_mode,
                                                          desk if ed is None else None)
            outputs["openai"] = openai_ed
            if ed is None:
                ed, delivered_provider = openai_ed, "openai"
        except Exception as e:
            diagnostics["openai"] = {"error": str(e)}
            print(f"OpenAI desk unavailable: {e}")

    if ed is None:
        delivered_provider = "emergency"
        ed = emergency_editorial(report, raw_headlines, bird)
        outputs["emergency"] = ed
        diagnostics["emergency"] = {"reason": "AI providers unavailable"}

    curated_quote = choose_quote(recent_editions)
    for output in outputs.values():
        output["quote"] = curated_quote
    ed["quote"] = curated_quote
    write_evaluation(delivered_provider, outputs, diagnostics)

    news = CONFIG["local_news"]
    district_label = (news.get("weekend_desk_name", "Local News") if NOW.weekday() >= 5
                      else news.get("desk_name", "Local News"))
    dims = render_editorial(ed, weather, report, birthdays, bird, district_label, "latest")

    # Keep a visually identical proof for the delivered desk and render every
    # shadow desk separately. These stay private in the Actions artifact.
    for provider, output in outputs.items():
        proof = HERE / f"proof-{provider}.png"
        if provider == delivered_provider:
            proof.write_bytes((HERE / "latest.png").read_bytes())
        else:
            render_editorial(output, weather, report, birthdays, bird, district_label,
                             f"proof-{provider}")

    archive = HERE / "archive" / f"bugle_{NOW:%Y-%m-%d}.png"
    archive.parent.mkdir(exist_ok=True)
    archive.write_bytes((HERE / "latest.png").read_bytes())
    remember_editorial(ed, recent_editions, bird)
    grade_note = report.display if report else "no grade (weather wire down)"
    bird_note = bird["common_name"] if bird else "Grounds Report fallback"
    print(f"Edition No. {VOLUME} ({dims}) ready via {delivered_provider}; "
          f"weather {grade_note}; {bird_note}.")

if __name__ == "__main__":
    sys.exit(main())
