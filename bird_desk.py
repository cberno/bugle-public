"""
Bird of the Day: one real eBird sighting near the house, with a real photograph.

eBird says which species were reported nearby this week. Wikipedia's lead
image (served from Wikimedia Commons, freely licensed) supplies the photograph,
with iNaturalist's Creative Commons taxon photo as the backup. The photo is
cropped, converted to grayscale, and tuned for e-ink before it reaches the
page, and the photographer and license are credited under it.

Every step can fail quietly: no key, no sightings, or no usable photo means
no featured bird, and the paper falls back to a text Grounds Report.
"""

import html
import io
import math
import random
import re
from urllib.parse import quote, unquote, urlparse

import requests
from PIL import Image, ImageFilter, ImageOps

NEAR_KM = 4.0            # "near the house" before widening to the full circle
SEARCH_KM = 8            # eBird radius
NO_REPEAT_EDITIONS = 21  # the whole rolling history
NOT_A_SPECIES = (" sp.", "/", " x ", "hybrid", "domestic", "feral")


# ---------------- eBird ----------------
def km_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def short_place(loc_name):
    """eBird hotspots read like 'Rock Creek Park--Picnic Grove 10'; keep the park."""
    place = re.split(r"--|\s—\s", loc_name or "")[0].strip(" -,")
    return place[:42].rstrip() if place else ""


def recent_sightings(api_key, lat, lon, headers, timeout=20):
    """Most recent report of each species within SEARCH_KM over the past week."""
    if not api_key:
        return []
    r = requests.get("https://api.ebird.org/v2/data/obs/geo/recent",
                     params=dict(lat=lat, lng=lon, dist=SEARCH_KM, back=7,
                                 cat="species", maxResults=200),
                     headers={**headers, "X-eBirdApiToken": api_key},
                     timeout=timeout)
    r.raise_for_status()
    out, seen = [], set()
    for o in r.json():
        name, sci = o.get("comName", ""), o.get("sciName", "")
        if not name or not sci or name in seen:
            continue
        if any(bad in f" {name.lower()} " for bad in NOT_A_SPECIES):
            continue
        seen.add(name)
        try:
            km = km_between(lat, lon, float(o["lat"]), float(o["lng"]))
        except (KeyError, TypeError, ValueError):
            km = float(SEARCH_KM)
        private = bool(o.get("locationPrivate"))
        out.append(dict(
            common_name=name,
            scientific_name=sci,
            species_code=o.get("speciesCode", ""),
            observed=o.get("obsDt", ""),
            place=None if private else short_place(o.get("locName")),
            km=round(km, 2),
        ))
    return out


def recently_featured(history):
    names = set()
    for edition in history[-NO_REPEAT_EDITIONS:]:
        if edition.get("bird"):
            names.add(edition["bird"].lower())
    return names


def candidate_order(sightings, history, seed):
    """Deterministic for the day (retries and proofs agree), varied across days."""
    featured = recently_featured(history)
    old_copy = " ".join(e.get("grounds", "") for e in history[-NO_REPEAT_EDITIONS:]).lower()
    pool = [s for s in sightings
            if s["common_name"].lower() not in featured
            and s["common_name"].lower() not in old_copy]
    if not pool:
        pool = list(sightings)
    near = [s for s in pool if s["km"] <= NEAR_KM]
    far = [s for s in pool if s["km"] > NEAR_KM]
    rng = random.Random(seed)
    near.sort(key=lambda s: s["species_code"])
    far.sort(key=lambda s: s["species_code"])
    rng.shuffle(near)
    rng.shuffle(far)
    return near + far


# ---------------- photographs ----------------
def clean_markup(value, limit=60):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def wikipedia_page(names, headers, timeout=20, log=print):
    """The species' Wikipedia summary (reference text and lead image)."""
    for title in names:
        r = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/"
                         + quote(title.replace(" ", "_")), headers=headers, timeout=timeout)
        if r.status_code != 200:
            log(f"Wikipedia: {title} returned HTTP {r.status_code}")
            continue
        page = r.json()
        if page.get("type") != "standard":
            log(f"Wikipedia: {title} is a {page.get('type')} page")
            continue
        return page
    return None


REFERENCE_SECTIONS = re.compile(
    r"description|identification|appearance|plumage|voice|vocali[sz]ation|call|song|"
    r"behavio|feeding|diet|distribution|habitat|range|migration", re.I)


def reference_from_article(text, limit=4500):
    """Lead section plus the sections that describe the bird in the field."""
    parts = re.split(r"\n={2,}\s*(.+?)\s*={2,}\n", "\n" + (text or ""))
    kept = [parts[0]]
    for title, body in zip(parts[1::2], parts[2::2]):
        if REFERENCE_SECTIONS.search(title) and body.strip():
            kept.append(f"{title}: {body}")
    joined = re.sub(r"\s+", " ", " ".join(kept)).strip()
    return joined[:limit]


def wikipedia_reference(page, headers, timeout=20):
    """The caption's fact base: the article's lead plus its field sections."""
    fallback = page.get("extract", "")
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", params=dict(
            action="query", format="json", formatversion=2, prop="extracts",
            explaintext=1, exsectionformat="wiki", redirects=1,
            titles=page.get("title", ""),
        ), headers=headers, timeout=timeout)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", [])
        text = (pages[0].get("extract") if pages else "") or ""
    except Exception:
        text = ""
    return reference_from_article(text) or re.sub(r"\s+", " ", fallback).strip()


def commons_filename(url):
    """File name from a Commons URL, original or /thumb/ (query strings ignored)."""
    path = urlparse(url).path
    parts = path.split("/")
    return unquote(parts[-2] if "/thumb/" in path and len(parts) > 1 else parts[-1])


def wikipedia_photo(page, headers, timeout=20, log=print):
    """The article's lead photograph, with its Commons credit."""
    image = page.get("originalimage") or {}
    source = image.get("source", "")
    if not source:
        log(f"Wikipedia: {page.get('title')} has no lead image")
        return None
    filename = commons_filename(source)
    if not filename.lower().endswith((".jpg", ".jpeg")) or image.get("width", 0) < 600:
        log(f"Wikipedia: lead image is not a usable photo ({filename})")
        return None  # maps, drawings, and small images are not the photograph we want
    meta = requests.get("https://en.wikipedia.org/w/api.php", params=dict(
        action="query", format="json", formatversion=2, titles=f"File:{filename}",
        prop="imageinfo", iiprop="url|extmetadata", iiurlwidth=1280,
    ), headers=headers, timeout=timeout).json()
    pages = meta.get("query", {}).get("pages", [])
    info = (pages[0].get("imageinfo") or [{}])[0] if pages else {}
    ext = info.get("extmetadata", {})
    license_name = clean_markup(ext.get("LicenseShortName", {}).get("value"), 30)
    if not license_name or "fair use" in license_name.lower():
        log(f"Wikipedia: no free license recorded for {filename}")
        return None
    artist = clean_markup(ext.get("Artist", {}).get("value"), 48) or "Wikimedia Commons"
    return dict(url=info.get("thumburl") or source,
                credit=f"Photo: {artist} · {license_name}")


def inaturalist_photo(scientific_name, headers, timeout=20):
    r = requests.get("https://api.inaturalist.org/v1/taxa", params=dict(
        q=scientific_name, rank="species", per_page=1), headers=headers, timeout=timeout)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results or results[0].get("name", "").lower() != scientific_name.lower():
        return None
    photo = results[0].get("default_photo") or {}
    if not photo.get("license_code") or not photo.get("medium_url"):
        return None  # all-rights-reserved photos stay off the wall
    return dict(url=photo["medium_url"].replace("/medium.", "/large."),
                credit=clean_markup(photo.get("attribution"), 70) + " · iNaturalist")


def crop_box(width, height, size, focus=None, fill=0.6):
    """Crop window of the frame's shape: centered on the bird and, when the bird
    is small, zoomed until it fills about `fill` of the frame — never past the
    frame's own resolution. Without a bird box, a gentle upper-center crop."""
    ratio = size[0] / size[1]
    max_w = min(width, height * ratio)
    if focus:
        bx0, by0, bx1, by1 = (focus[0] * width, focus[1] * height,
                              focus[2] * width, focus[3] * height)
        need_w = max((bx1 - bx0) / fill, (by1 - by0) / fill * ratio, size[0])
        crop_w = min(max_w, need_w)
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    else:
        crop_w = max_w
        cx, cy = width * 0.5, height * 0.45
    crop_h = crop_w / ratio
    left = min(max(0.0, cx - crop_w / 2), width - crop_w)
    top = min(max(0.0, cy - crop_h / 2), height - crop_h)
    return (round(left), round(top), round(left + crop_w), round(top + crop_h))


def prepare_photo(raw, size, focus=None):
    """Crop to the frame around the bird, then grayscale with a gentle lift for e-ink."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    image = image.convert("L")
    if min(image.size) < 400:
        raise ValueError(f"photo too small: {image.size}")
    image = image.crop(crop_box(*image.size, size, focus)).resize(size, Image.LANCZOS)
    image = ImageOps.autocontrast(image, cutoff=1)
    image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=2))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def featured_bird(api_key, lat, lon, history, seed, headers, photo_path, photo_size,
                  attempts=8, verify=None, max_checks=8, log=print):
    """Pick today's bird and save its prepared photo; None if nothing qualifies.

    verify(raw_bytes, bird) -> dict(ok, box, reason) is the photo check. If it
    raises (desk unavailable), the photo runs unchecked with a plain crop; if it
    says no, the next photo or bird is tried.
    """
    try:
        sightings = recent_sightings(api_key, lat, lon, headers)
    except Exception as e:
        log(f"eBird desk failed: {e}")
        return None
    checks = 0
    for bird in candidate_order(sightings, history, seed)[:attempts]:
        names = (bird["scientific_name"], bird["common_name"])
        try:
            page = wikipedia_page(names, headers, log=log)
        except Exception as e:
            log(f"Wikipedia lookup failed for {bird['common_name']}: {e}")
            page = None
        if not page:
            log(f"Bird desk skipped {bird['common_name']}: no reference article to check facts against")
            continue
        reference = wikipedia_reference(page, headers)
        finders = (lambda: wikipedia_photo(page, headers, log=log),
                   lambda: inaturalist_photo(bird["scientific_name"], headers))
        for finder in finders:
            try:
                photo = finder()
                if not photo:
                    continue
                raw = requests.get(photo["url"], headers=headers, timeout=30)
                raw.raise_for_status()
                raw = raw.content
            except Exception as e:
                log(f"Photo desk skipped {bird['common_name']}: {e}")
                continue
            review = {"status": "unchecked"}
            if verify and checks < max_checks:
                checks += 1
                try:
                    review = verify(raw, bird)
                except Exception as e:
                    log(f"Photo check unavailable ({e}); running {bird['common_name']} unchecked")
                    review = {"status": "unchecked", "error": str(e)[:200]}
                else:
                    if not review.get("ok"):
                        log(f"Photo check rejected {bird['common_name']} "
                            f"({photo['credit']}): {review.get('reason')}")
                        continue
                    review["status"] = "passed"
            elif verify:
                log("Photo check budget spent for today; stopping the bird desk")
                return None
            try:
                photo_path.write_bytes(prepare_photo(raw, photo_size, review.get("box")))
            except Exception as e:
                log(f"Photo desk skipped {bird['common_name']}: {e}")
                continue
            log(f"Bird desk: {bird['common_name']}, {photo['credit']}; "
                f"photo check {review['status']}")
            return dict(bird, photo=photo_path.resolve().as_uri(), credit=photo["credit"],
                        summary=page.get("extract", "")[:900], reference=reference,
                        photo_check={k: v for k, v in review.items() if k != "box"},
                        miles=round(bird["km"] * 0.621371, 1))
    log("Bird desk: no sighting with a usable, checked photograph today.")
    return None
