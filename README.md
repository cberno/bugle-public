# A household newspaper for your wall

One page, printed fresh every morning at 4am, on an e-ink display that looks
like paper. No screen glow, no feeds to scroll, no notifications. It reads:

- **The masthead.** Your paper, your name for it.
- **Morning Line.** A short quotation from a curated list, never repeated soon.
- **Weather.** A letter grade from A+ to F for how usable the day is, from a
  fixed rubric (not an AI's mood), plus one plain sentence on why.
- **Local News.** Up to three fresh stories from the neighborhood feeds you
  choose, rewritten to one line each. Never yesterday's news twice.
- **Birthdays.** First names only, when your calendar has one today (optional).
- **Bird of the Day.** A real bird reported to eBird near your house this
  week, with a large, credited photograph and a two-sentence caption.
- **Your motto** at the bottom.

Every factual line is checked before it prints: a second AI pass confirms the
bird in the photo is the species named, and checks the caption and weather
lines against their sources. Anything it can't confirm gets rewritten or
replaced with plain sourced text.

**To set one up, follow [SETUP.md](SETUP.md).** About an hour, mostly waiting
on accounts. You'll need a GitHub account, an Anthropic API key (a few dollars
a month), a free eBird key, and a TRMNL X display.

Setup is one button: tell it where you live and what to call the paper, and
it finds your weather, time zone, local news sources (testing every feed), and
the right-sized circle for nearby birds.

## How it works

```
 every morning, on GitHub's servers (free)
 ┌──────────────────────────────────────────────────────────────┐
 │ weather (Open-Meteo, NWS) ─┐                                 │
 │ local news (your RSS feeds) ┼─► editor (Claude) ─► copy desk ─┼─► latest.png
 │ eBird + Wikipedia photo ────┘        ▲             (checks)  │      │
 │ your calendar (birthdays) ───────────┘                       │      │
 └──────────────────────────────────────────────────────────────┘      │
                                                                        ▼
                   your public "display" repo on GitHub Pages ── TRMNL X on the wall
```

Your code, keys, and history stay in your **private** repository. Only the
finished image goes to the public display repository the TRMNL reads from.

## Files

| File | What it is |
|---|---|
| `bugle.toml` | **Your paper.** Name, motto, location, household, weather preferences, news feeds, birds. Written by setup; edit freely. |
| `setup_paper.py` | The "Set up my paper" button: finds your place, feeds, and bird circle |
| `bugle_action.py` | The newsroom: gathers the inputs, runs the editor, renders the page |
| `trmnl_template.html` | The page layout (HTML/CSS, rendered to a 1404×1872 grayscale PNG) |
| `grade_engine.py` | The weather grade: a deterministic rubric, each factor graded, worst one wins |
| `bird_desk.py` | Bird of the Day: eBird sighting, Wikipedia/iNaturalist photo, bird-aware crop |
| `copy_desk.py` | The checks: photo verification and fact-checking against sources |
| `.github/workflows/bugle.yml` | "Print the paper": early-morning schedule and publishing |
| `.github/workflows/setup.yml` | "Set up my paper": runs `setup_paper.py` and saves `bugle.toml` |
| `editorial_history.json` | The paper's memory, so quotes, stories, and birds don't repeat. Starts empty. |
| `tests/` | `python -m unittest discover -s tests` |

## Making changes

Small changes (name, motto, feeds, weather preferences) are one edit in
`bugle.toml`. Moved? Run "Set up my paper" again.
For anything bigger, such as a new section, a different layout, or a change in
tone, the easiest way is to point Claude at your repository and describe what
you want. SETUP.md, Part 7, covers this.

## Credits

Weather and place lookup from [Open-Meteo](https://open-meteo.com) and the
[National Weather Service](https://www.weather.gov). Bird sightings from
[eBird](https://ebird.org) (Cornell Lab of Ornithology). Photos from
[Wikimedia Commons](https://commons.wikimedia.org) and
[iNaturalist](https://www.inaturalist.org), credited on the page under their
Creative Commons licenses. Fonts: EB Garamond and Playfair Display, both under
the SIL Open Font License (see `fonts/README.md`). The weather grade rubric
comes from the Bugle Weather app.
