"""
The copy desk: a second, skeptical read before anything prints.

- check_photo looks at a candidate photograph and confirms it is a real photo
  of the named species with the bird clearly visible, and says where the bird
  is so the crop keeps it whole.
- check_copy compares the bird caption and the weather lines against the
  source material (reference text, eBird report, forecast numbers, today's
  date) and lists anything unsupported.

Both take an `ask` callable: ask(content_blocks) -> parsed JSON dict. The
caller decides what an outage means; a "no" from the desk is final for that
candidate.
"""

import base64
import io
import json
import re

from PIL import Image, ImageOps

PHOTO_PROMPT = """This photograph is about to print as the "Bird of the Day" in a family
newspaper, captioned {common} ({scientific}). Look carefully and reply with
JSON only, no other text:
{{"species_match": "yes" | "no" | "unsure",
  "live_bird_photo": true | false,
  "bird_clearly_visible": true | false,
  "box": [x0, y0, x1, y1],
  "reason": "one short sentence"}}

species_match: "yes" only if the bird shown is a {common}. "unsure" if you
cannot tell. live_bird_photo: a photograph of a living wild bird, not a map,
drawing, painting, egg, nest without the bird, museum specimen, or a bird in
someone's hand. bird_clearly_visible: the bird is the main subject, in focus,
not tiny, and not mostly hidden. box: the whole bird (including tail and bill)
as fractions of image width and height, 0 to 1."""

COPY_PROMPT = """You are the copy desk of a small family newspaper. Check the two items
below against the SOURCE material only. Do not edit style; find errors.

Today is {date_line}.

ITEM 1 — WEATHER LINES
verdict: {verdict}
editorial: {editorial}
SOURCE: forecast {weather}
SOURCE: official grade {grade}; engine factors {factors}
SOURCE: NWS alerts {alerts}

ITEM 2 — BIRD CAPTION (printed under the name {common}, {scientific})
caption: {caption}
SOURCE: eBird report {report}
SOURCE: reference text: {reference}

Flag a claim if it is not supported by its SOURCE, contradicts it, or says
something about today's date, weekday, or a holiday that is not true.
Leaving something out is never an error; neither is wording that differs from
the source but means the same thing ("sparrow-like" for "a sparrow").
Weather: plain descriptions that follow directly from the numbers are fine
("crisp" for a 51°F high, "dry" with no rain); every number must match.
Bird: every factual claim (size, comparisons to other birds, calls, field
marks, range, how common it is, behavior, season) must be stated in the
reference text or the eBird report. General knowledge that the reference does
not state is NOT enough. Watch-for / listen-for phrasing is fine if the thing
to watch for is supported.

Reply with JSON only, no other text:
{{"weather": {{"verdict": "pass" | "fail", "errors": ["..."]}},
  "bird": {{"verdict": "pass" | "fail", "errors": ["..."]}}}}
verdict is "fail" only when at least one claim is wrong or unsupported. errors
lists only those claims, each one short sentence quoting the offending words
and saying what the source supports instead. Never list claims you found
acceptable, and leave errors empty when the verdict is "pass"."""


# Desks sometimes narrate a pass inside the error list; that is not an error.
NOT_AN_ERROR = re.compile(r"\bno (?:factual )?errors?\b|\bnot (?:a|an) (?:factual )?error\b|"
                          r"\bis (?:fully |actually )?supported\b", re.I)


def preview_jpeg(raw, max_side=1024):
    """A modest JPEG for the desk to look at (keeps the request small)."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    image.thumbnail((max_side, max_side))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode("ascii")


def normalize_box(box):
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in box)
    except (TypeError, ValueError):
        return None
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        return None
    return [x0, y0, x1, y1]


def check_photo(raw, bird, ask):
    """Return dict(ok, box, reason). Raises if the desk itself is unavailable."""
    result = ask([
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": preview_jpeg(raw)}},
        {"type": "text", "text": PHOTO_PROMPT.format(common=bird["common_name"],
                                                     scientific=bird["scientific_name"])},
    ])
    if not isinstance(result, dict):
        raise ValueError("photo desk returned no verdict")
    ok = (result.get("species_match") == "yes"
          and result.get("live_bird_photo") is True
          and result.get("bird_clearly_visible") is True)
    return dict(ok=ok, box=normalize_box(result.get("box")),
                reason=str(result.get("reason", ""))[:200],
                species_match=result.get("species_match"))


def check_copy(ed, facts, ask):
    """Return dict(weather=[problems], bird=[problems]). Raises if unavailable."""
    bird = facts.get("bird") or {}
    caption = ed["grounds"] if bird else "(no bird today; skip this item)"
    result = ask([{"type": "text", "text": COPY_PROMPT.format(
        date_line=facts["date_line"],
        verdict=ed["weather"]["verdict"], editorial=ed["weather"]["editorial"],
        weather=json.dumps(facts.get("weather"), ensure_ascii=False),
        grade=facts.get("grade") or "none (forecast wire down)",
        factors=json.dumps(facts.get("factors", []), ensure_ascii=False),
        alerts=json.dumps(facts.get("alerts", []), ensure_ascii=False),
        common=bird.get("common_name", "—"), scientific=bird.get("scientific_name", "—"),
        caption=caption,
        report=json.dumps(bird.get("report", {}), ensure_ascii=False),
        reference=bird.get("reference", "") or "(none)",
    )}])
    if not isinstance(result, dict):
        raise ValueError("copy desk returned no verdict")

    def problems(key):
        item = result.get(key)
        if not isinstance(item, dict) or str(item.get("verdict", "")).lower() != "fail":
            return []
        errors = item.get("errors") if isinstance(item.get("errors"), list) else []
        kept = [str(e)[:240] for e in errors
                if str(e).strip() and not NOT_AN_ERROR.search(str(e))]
        return kept

    return dict(weather=problems("weather"), bird=problems("bird") if bird else [])
