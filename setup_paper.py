#!/usr/bin/env python3
"""
Set up the paper for where you live.

    python setup_paper.py --place "Takoma Park, MD" [--name "The Takoma Times"]
                          [--household "..."] [--dry-run]

Run by the "Set up my paper" workflow. It:
  1. finds the place (Open-Meteo geocoding): coordinates, time zone, country
  2. sets weather units (US or metric) and whether NWS alerts apply
  3. finds local news feeds: asks Claude for local outlets (if a key is set),
     then fetches every candidate and keeps only feeds that work and are
     current; Google News for the town is the backstop
  4. sizes the eBird search circle so there are enough birds to choose from
  5. rewrites bugle.toml, keeping your name, motto, household, and preferences

Nothing here is trusted without checking: every feed is fetched, every
number comes from an API, and the summary says what was chosen and why.
"""

import argparse
import datetime as dt
import json
import os
import re
import time
import tomllib
from pathlib import Path
from urllib.parse import quote, urlparse

import feedparser
import requests

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "bugle.toml"
UA = {"User-Agent": "HouseholdBugle-setup/1.0 (github.com/"
                    f"{os.environ.get('GITHUB_REPOSITORY', 'household-bugle')})"}
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
TEMPLATE_DEFAULTS = {"edition": "Hometown Edition", "launch_date": dt.date(2026, 12, 1)}
METRIC_EXCEPT = {"US", "LR", "MM"}          # countries that use Fahrenheit
BIRD_RADII_KM = (8, 15, 25, 40)
BIRDS_WANTED = 25                            # species in the circle before we stop widening
MAX_FEEDS = 4
FEED_MAX_AGE_DAYS = 21

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}


def log(msg):
    print(msg, flush=True)


def fetch(url, attempts=3, pause=3, **kw):
    """GET with retries; public APIs here occasionally stall for a few seconds."""
    kw.setdefault("timeout", 20)
    kw.setdefault("headers", UA)
    for attempt in range(attempts):
        try:
            r = requests.get(url, **kw)
            if r.status_code < 500:
                return r
        except requests.RequestException:
            if attempt == attempts - 1:
                raise
        time.sleep(pause * 3 ** attempt)
    return r


# ---------------- 1. where ----------------
def split_place(text):
    """'Takoma Park, MD' -> ('Takoma Park', 'md'); '20912' -> ('20912', '')."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise SystemExit("Tell me where you live: a town, neighborhood, or ZIP code.")
    return parts[0], " ".join(parts[1:]).lower()


def qualifier_matches(result, qualifier):
    if not qualifier:
        return True
    words = {qualifier, US_STATES.get(qualifier.upper(), "").lower()}
    fields = [str(result.get(k, "")).lower()
              for k in ("admin1", "admin2", "admin3", "country", "country_code")]
    return any(w and (w == f or f.startswith(w)) for w in words for f in fields)


def choose_place(results, qualifier):
    matches = [r for r in results if qualifier_matches(r, qualifier)] or \
              ([] if qualifier else results)
    if not matches:
        return None
    # Prefer populated places; among those, the biggest.
    return max(matches, key=lambda r: (r.get("feature_code", "").startswith("PPL"),
                                       r.get("population") or 0))


def geocode(text):
    name, qualifier = split_place(text)
    params = dict(name=name, count=10, language="en", format="json")
    if re.fullmatch(r"\d{5}", name):
        params["countryCode"] = "US"
    try:
        r = fetch("https://geocoding-api.open-meteo.com/v1/search", params=params)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SystemExit("The place lookup service (Open-Meteo) didn't answer "
                         f"({type(e).__name__}). Run setup again in a minute.")
    place = choose_place(r.json().get("results") or [], qualifier)
    if not place:
        raise SystemExit(f"Couldn't find '{text}'. Try a town and state, like "
                         "'Takoma Park, Maryland', or a ZIP code.")
    if re.fullmatch(r"\d{5}", name):
        place = dict(place, searched_zip=name)
    return place


def describe(place):
    region = place.get("admin1") if place.get("country_code") == "US" else place.get("country")
    if region and region != place.get("name"):
        return f"{place['name']}, {region}"
    return place["name"]


# ---------------- 2. local news ----------------
def google_news_feeds(place):
    cc = (place.get("country_code") or "US").upper()
    tail = f"hl=en-{cc}&gl={cc}&ceid={cc}:en"
    where = describe(place)
    return [
        dict(name=f"Google News: {where}",
             url=f"https://news.google.com/rss/search?q={quote(chr(34) + place['name'] + chr(34))}"
                 f"+{quote(place.get('admin1') or place.get('country') or '')}+when:7d&{tail}"),
        dict(name=f"Google News: {where} (local section)",
             url=f"https://news.google.com/rss/headlines/section/geo/{quote(where)}?{tail}"),
    ]


def ask_claude_for_local_sources(place, api_key):
    """Candidate outlets and nearby neighborhood names. Nothing is trusted unchecked."""
    prompt = f"""A household newspaper is being set up for someone living in
{describe(place)} ({place.get('country')}), near latitude {place['latitude']:.2f},
longitude {place['longitude']:.2f}.

Reply with JSON only:
{{"neighborhoods": ["..."], "outlets": [{{"name": "...", "feed": "https://...", "site": "https://..."}}]}}

neighborhoods: up to 6 neighborhoods or towns within about 5 miles, closest first,
starting with the place itself.
outlets: up to 8 sources of genuinely local news for this place, most local first:
neighborhood blogs, the local paper, community news sites, local TV or radio news,
city or county government news. Give the RSS or Atom feed URL if you know it, and
the site's home page either way. Only real outlets; every feed will be tested."""
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"}, json={
        "model": CLAUDE_MODEL, "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
    start = text.find("{")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    hoods = [str(n).strip() for n in data.get("neighborhoods", []) if str(n).strip()][:6]
    outlets = [o for o in data.get("outlets", []) if isinstance(o, dict)][:8]
    return hoods, outlets


def feed_candidates(outlet):
    """The feed URL given, then the usual feed addresses on the outlet's site."""
    urls = []
    if outlet.get("feed"):
        urls.append(outlet["feed"])
    site = outlet.get("site") or outlet.get("feed") or ""
    parsed = urlparse(site)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        root = f"{parsed.scheme}://{parsed.netloc}"
        urls += [root + path for path in ("/feed/", "/rss", "/feed", "/rss.xml", "/index.xml")]
    seen, out = set(), []
    for u in urls:
        if u not in seen and urlparse(u).scheme in ("http", "https"):
            seen.add(u)
            out.append(u)
    return out[:4]


def check_feed(url, now=None):
    """dict(ok, items, newest_days, reason). A feed passes if it parses, has a few
    items, and has published something recently."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        r = fetch(url, attempts=2, pause=2, timeout=12)
        if r.status_code != 200:
            return dict(ok=False, reason=f"HTTP {r.status_code}")
        feed = feedparser.parse(r.content)
    except Exception as e:
        return dict(ok=False, reason=type(e).__name__)
    entries = feed.entries or []
    if len(entries) < 3:
        return dict(ok=False, reason=f"only {len(entries)} items")
    stamps = [e.get("published_parsed") or e.get("updated_parsed") for e in entries]
    stamps = [dt.datetime(*s[:6], tzinfo=dt.timezone.utc) for s in stamps if s]
    newest = min(((now - s).days for s in stamps), default=None)
    if newest is not None and newest > FEED_MAX_AGE_DAYS:
        return dict(ok=False, reason=f"last post {newest} days ago")
    return dict(ok=True, items=len(entries), newest_days=newest,
                title=(feed.feed.get("title") or "").strip())


def find_feeds(place, api_key, checker=check_feed):
    """Validated local feeds, most local first, plus the notes for the summary."""
    notes, kept, hoods = [], [], []
    if api_key:
        try:
            hoods, outlets = ask_claude_for_local_sources(place, api_key)
        except Exception as e:
            outlets = []
            notes.append(f"Couldn't ask Claude for local outlets ({type(e).__name__}); "
                         "using Google News only.")
        for outlet in outlets:
            if len(kept) >= MAX_FEEDS - 1:
                break
            for url in feed_candidates(outlet):
                result = checker(url)
                if result["ok"]:
                    kept.append(dict(name=outlet.get("name") or result.get("title") or url,
                                     url=url, **result))
                    break
            else:
                notes.append(f"Skipped {outlet.get('name', 'an outlet')}: no working, "
                             "current feed found.")
    else:
        notes.append("No ANTHROPIC_API_KEY yet, so only Google News was used. Add the key "
                     "and run setup again to look for neighborhood sources.")
    for gn in google_news_feeds(place):
        if len(kept) >= MAX_FEEDS:
            break
        result = checker(gn["url"])
        if result["ok"]:
            kept.append(dict(gn, **result))
        else:
            notes.append(f"Skipped {gn['name']}: {result['reason']}.")
    return kept, hoods, notes


def focus_text(place, hoods):
    near = [h for h in hoods if h] or [place["name"]]
    wider = place.get("admin2") or place.get("admin1") or "the region"
    return (f"Strongly prefer {', '.join(near)}; then stories from elsewhere in "
            f"{wider} that touch daily life there. Neighborhood sources outrank wire "
            "copy when they carry real news.")


# ---------------- 3. birds ----------------
def ebird_species_count(lat, lon, km, key):
    r = fetch("https://api.ebird.org/v2/data/obs/geo/recent", params=dict(
        lat=lat, lng=lon, dist=km, back=14, cat="species", maxResults=500),
        headers={**UA, "X-eBirdApiToken": key})
    r.raise_for_status()
    return len({o.get("speciesCode") for o in r.json()})


def nearest_hotspots(lat, lon, key, n=3):
    r = fetch("https://api.ebird.org/v2/ref/hotspot/geo", params=dict(
        lat=lat, lng=lon, dist=15, fmt="json"),
        headers={**UA, "X-eBirdApiToken": key})
    r.raise_for_status()
    spots = r.json()
    spots.sort(key=lambda h: (h.get("lat", 0) - lat) ** 2 + (h.get("lng", 0) - lon) ** 2)
    return [h.get("locName", "") for h in spots[:n] if h.get("locName")]


def tune_birds(lat, lon, key, counter=ebird_species_count):
    """Smallest circle with enough recent species. Cities stay tight; farms widen."""
    if not key:
        return None, None, ["No EBIRD_API_KEY yet, so the bird circle stays at 8 km. Add "
                            "the key and run setup again to size it for your area."]
    notes = []
    for km in BIRD_RADII_KM:
        try:
            count = counter(lat, lon, km, key)
        except Exception as e:
            notes.append(f"eBird didn't answer ({type(e).__name__}); keeping 8 km.")
            return None, None, notes
        if count >= BIRDS_WANTED:
            return km, count, notes
    return BIRD_RADII_KM[-1], count, notes


# ---------------- 4. write the settings ----------------
def toml_str(value):
    """A TOML basic string (JSON escapes are valid TOML when non-ASCII is left raw)."""
    return json.dumps(str(value), ensure_ascii=False)


def load_current():
    try:
        return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def build_config(current, place, feeds, hoods, radius_km, name=None, household=None,
                 today=None, header=None):
    today = today or dt.date.today()
    paper = current.get("paper", {})
    home = current.get("household", {})
    news = current.get("local_news", {})
    weather = current.get("weather", {})
    birds = current.get("birds", {})
    grounds = current.get("grounds", {})

    edition = paper.get("edition", "")
    if not edition or edition == TEMPLATE_DEFAULTS["edition"]:
        edition = f"{place['name']} Edition"
    launch = paper.get("launch_date")
    if not launch or launch == TEMPLATE_DEFAULTS["launch_date"] or launch > today:
        launch = today
    cc = (place.get("country_code") or "").upper()
    units = "us" if cc in METRIC_EXCEPT else "metric"
    skip = birds.get("skip", [])
    prefs = {k: int(weather.get(k, 0)) for k in
             ("warmth", "humidity_tolerance", "rain_tolerance", "sun_preference",
              "wind_tolerance")}
    feed_lines = "\n".join(f"  {toml_str(f['url'])},   # {f['name']}" for f in feeds) or \
        "  # none found yet: add your neighborhood's news feeds here"

    header = header or (
        f'#  Written by "Set up my paper" for {describe(place)}. Edit anything here\n'
        "#  (click the file, then the pencil icon); the next edition obeys. Running\n"
        "#  setup again re-finds location, news, and birds, and keeps the rest.")
    return f'''# ──────────────────────────────────────────────────────────────────────────
#  YOUR NEWSPAPER
{header}
# ──────────────────────────────────────────────────────────────────────────

[paper]
# The masthead. Anything you like. Under about 24 characters looks best.
name = {toml_str(name or paper.get("name", "The Morning Bugle"))}

# Printed in the dateline after the date.
edition = {toml_str(edition)}

# The line at the bottom of every page.
motto = {toml_str(paper.get("motto", "Do something you will be proud of today."))}

# Vol. I. The volume number counts up by one every day from here.
launch_date = {launch.isoformat()}


[location]
# Found by setup. Rounded to two decimals (about half a mile), so your exact
# address is never stored.
latitude = {place["latitude"]:.2f}
longitude = {place["longitude"]:.2f}
description = {toml_str(describe(place))}
country_code = {toml_str(cc)}

# The paper prints in the early morning in this time zone.
timezone = {toml_str(place.get("timezone") or "America/New_York")}


[household]
# Who reads the paper, in a sentence. The editor uses this for tone and
# context only.
description = {toml_str(household or home.get("description", "a household"))}

# The editorial voice in a few words.
voice = {toml_str(home.get("voice", "dry, warm, terse"))}


[weather]
# "us" (°F, mph) or "metric" (°C, km/h).
units = {toml_str(units)}

# How your household likes its weather, from -2 to +2. Zero is the standard
# rubric. These nudge the A-to-F grade; they never hide a weather warning.
warmth = {prefs["warmth"]}              # +2 if you love heat, -2 if you'd rather it cool
humidity_tolerance = {prefs["humidity_tolerance"]}  # +2 if muggy days don't bother you
rain_tolerance = {prefs["rain_tolerance"]}      # +2 if a few showers are fine by you
sun_preference = {prefs["sun_preference"]}      # +2 if you really want sun; -2 if clouds are fine
wind_tolerance = {prefs["wind_tolerance"]}      # +2 if wind doesn't bother you


[local_news]
desk_name = {toml_str(news.get("desk_name", "Local News"))}
weekend_desk_name = {toml_str(news.get("weekend_desk_name", "Local News — Weekend"))}

# Found and tested by setup, most local first. Add your own favorites:
# most news sites have a feed at /feed/ or /rss.
feeds = [
{feed_lines}
]

# The places the editor should favor, closest first. Plain English.
focus = {toml_str(focus_text(place, hoods))}


[birds]
# Bird of the Day comes from eBird reports inside this circle (kilometers).
# Setup sized it so there are enough species to choose from.
radius_km = {radius_km or birds.get("radius_km", 8)}

# How many days back to look for reports.
days_back = {int(birds.get("days_back", 7))}

# Species never to feature, by common name, e.g. ["European Starling"].
skip = {json.dumps(list(skip), ensure_ascii=False)}


[grounds]
# Optional. On the rare day there's no bird, the bottom section becomes a
# short Grounds Report about your yard, written from this. Leave it empty and
# the editor writes a seasonal Almanac note for your area instead.
description = {toml_str(grounds.get("description", ""))}
'''


def summary(place, feeds, hoods, radius_km, species, hotspots, notes, config_text, dry_run):
    cfg = tomllib.loads(config_text)
    lines = [
        f"## {'Preview' if dry_run else 'Set up'}: {cfg['paper']['name']}",
        "",
        f"**Place:** {describe(place)} — {cfg['location']['latitude']}, "
        f"{cfg['location']['longitude']} (time zone {cfg['location']['timezone']})",
        f"**Weather:** {cfg['weather']['units']} units"
        + ("; National Weather Service alerts included" if place.get("country_code") == "US"
           else "; weather alerts are only available in the US"),
        f"**Birds:** {cfg['birds']['radius_km']} km circle"
        + (f", {species} species reported in the past two weeks" if species else ""),
    ]
    if hotspots:
        lines.append(f"**Nearest eBird hotspots:** {', '.join(hotspots)}")
    if hoods:
        lines.append(f"**Editor will favor:** {', '.join(hoods)}")
    lines += ["", "**Local news feeds (each one fetched and checked):**", ""]
    lines += [f"- {f['name']} — {f['items']} items"
              + (f", newest {f['newest_days']} days old" if f.get("newest_days") is not None else "")
              for f in feeds] or ["- none found; add feeds by hand in bugle.toml"]
    if notes:
        lines += ["", "**Notes:**", ""] + [f"- {n}" for n in notes]
    lines += ["", "Not the right place? Run setup again with more detail, e.g. "
                  "'Takoma Park, Maryland' or a ZIP code."]
    if dry_run:
        lines += ["", "This was a preview; nothing was saved."]
    else:
        lines += ["", "Next: Actions → **Print the paper** → Run workflow with **Dry run** "
                      "checked to see your first edition."]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--place", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--household", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    ebird_key = os.environ.get("EBIRD_API_KEY", "")

    place = geocode(args.place)
    log(f"Place: {describe(place)} ({place['latitude']:.2f}, {place['longitude']:.2f}), "
        f"{place.get('timezone')}")
    feeds, hoods, notes = find_feeds(place, anthropic_key)
    log(f"Feeds kept: {[f['name'] for f in feeds]}")
    radius, species, bird_notes = tune_birds(place["latitude"], place["longitude"], ebird_key)
    notes += bird_notes
    hotspots = []
    if ebird_key:
        try:
            hotspots = nearest_hotspots(place["latitude"], place["longitude"], ebird_key)
        except Exception:
            pass

    text = build_config(load_current(), place, feeds, hoods, radius,
                        name=args.name.strip() or None,
                        household=args.household.strip() or None)
    tomllib.loads(text)  # never write a settings file the press can't read
    report = summary(place, feeds, hoods, radius, species, hotspots, notes, text, args.dry_run)
    print("\n" + report + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(report + "\n\n<details><summary>bugle.toml</summary>\n\n```toml\n"
                      + text + "```\n</details>\n")
    if args.dry_run:
        print(text)
    else:
        CONFIG_PATH.write_text(text, encoding="utf-8")
        log("Saved bugle.toml")


if __name__ == "__main__":
    main()
