import io
import unittest
from unittest.mock import patch

from PIL import Image

import bird_desk
import bugle_action as bugle
import copy_desk
import grade_engine as ge


def jpeg(size=(1200, 800)):
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 140, 90)).save(buf, "JPEG")
    return buf.getvalue()


BIRD = dict(common_name="Common Raven", scientific_name="Corvus corax", species_code="comrav",
            observed="2026-11-23 10:44", place="Rock Creek Park", km=3.7, miles=2.3,
            reference="The common raven (Corvus corax) is a large all-black passerine bird. "
                      "It is the most widely distributed of all corvids.",
            summary="")


class PhotoCheckTests(unittest.TestCase):
    def test_clear_photo_of_the_species_passes_with_box(self):
        ask = lambda content: {"species_match": "yes", "live_bird_photo": True,
                               "bird_clearly_visible": True, "box": [0.1, 0.2, 0.5, 0.9],
                               "reason": "A raven on grass."}
        result = copy_desk.check_photo(jpeg(), BIRD, ask)
        self.assertTrue(result["ok"])
        self.assertEqual(result["box"], [0.1, 0.2, 0.5, 0.9])

    def test_unsure_species_or_specimen_is_rejected(self):
        for verdict in ({"species_match": "unsure", "live_bird_photo": True, "bird_clearly_visible": True},
                        {"species_match": "yes", "live_bird_photo": False, "bird_clearly_visible": True}):
            self.assertFalse(copy_desk.check_photo(jpeg(), BIRD, lambda c, v=verdict: v)["ok"])

    def test_photo_check_sends_an_image(self):
        seen = {}
        def ask(content):
            seen["types"] = [block["type"] for block in content]
            return {"species_match": "yes", "live_bird_photo": True, "bird_clearly_visible": True}
        copy_desk.check_photo(jpeg((3000, 2000)), BIRD, ask)
        self.assertEqual(seen["types"], ["image", "text"])

    def test_bad_boxes_are_ignored(self):
        self.assertIsNone(copy_desk.normalize_box(None))
        self.assertIsNone(copy_desk.normalize_box([0.5, 0.5, 0.5, 0.5]))
        self.assertEqual(copy_desk.normalize_box([-1, 0, 2, 1]), [0.0, 0.0, 1.0, 1.0])


class CropTests(unittest.TestCase):
    def test_crop_follows_a_bird_on_the_left(self):
        left, top, right, bottom = bird_desk.crop_box(1600, 1000, (634, 641), [0.02, 0.3, 0.25, 0.8])
        self.assertEqual(left, 0)
        self.assertAlmostEqual((right - left) / (bottom - top), 634 / 641, places=2)

    def test_small_bird_is_zoomed_but_never_below_frame_size(self):
        left, top, right, bottom = bird_desk.crop_box(1280, 960, (634, 641), [0.45, 0.45, 0.55, 0.55])
        self.assertLess(right - left, 949)       # tighter than the widest possible crop
        self.assertGreaterEqual(right - left, 634)

    def test_no_box_means_plain_crop(self):
        left, top, right, bottom = bird_desk.crop_box(1280, 960, (634, 641))
        self.assertEqual(bottom - top, 960)


class BirdDeskVerifyTests(unittest.TestCase):
    def test_rejected_photo_falls_through_to_the_next_source(self):
        calls = []
        def verify(raw, bird):
            calls.append(bird["common_name"])
            return {"ok": len(calls) > 1, "box": None, "reason": "test"}
        sighting = dict(BIRD)
        sighting.pop("reference"); sighting.pop("summary"); sighting.pop("miles")
        get = lambda *a, **k: type("R", (), {"content": jpeg(), "raise_for_status": lambda self: None})()
        with patch.object(bird_desk, "recent_sightings", return_value=[sighting]), \
             patch.object(bird_desk, "wikipedia_page", return_value={"title": "Common raven", "extract": "x"}), \
             patch.object(bird_desk, "wikipedia_reference", return_value="ref"), \
             patch.object(bird_desk, "wikipedia_photo", return_value={"url": "u1", "credit": "Photo: A"}), \
             patch.object(bird_desk, "inaturalist_photo", return_value={"url": "u2", "credit": "B · iNaturalist"}), \
             patch.object(bird_desk.requests, "get", side_effect=get):
            import tempfile, pathlib
            path = pathlib.Path(tempfile.mkdtemp()) / "b.png"
            bird = bird_desk.featured_bird("k", 0, 0, [], "s", {}, path, (634, 641),
                                           verify=verify, log=lambda *_: None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(bird["credit"], "B · iNaturalist")
        self.assertEqual(bird["photo_check"]["status"], "passed")

    def test_bird_without_reference_article_is_skipped(self):
        sighting = {k: v for k, v in BIRD.items() if k not in ("reference", "summary", "miles")}
        with patch.object(bird_desk, "recent_sightings", return_value=[sighting]), \
             patch.object(bird_desk, "wikipedia_page", return_value=None):
            self.assertIsNone(bird_desk.featured_bird("k", 0, 0, [], "s", {}, None, (634, 641),
                                                      log=lambda *_: None))


class CopyDeskTests(unittest.TestCase):
    def setUp(self):
        hours = [ge.Hour(hour=h, temperature_f=50, relative_humidity=50, dew_point_f=30)
                 for h in range(24)]
        self.report = ge.grade(ge.Day(date_key="2026-11-24", high_f=51, low_f=36, hourly=hours))
        self.ed = {"quote": {"text": "", "attr": ""},
                   "weather": {"verdict": "Clear and crisp for the eve of Thanksgiving",
                               "editorial": "High of 51°F."},
                   "headlines": {"local": [{"head": "H", "line": "L"}]},
                   "grounds": "All black and crow-sized; listen for a deep croak."}

    def desk(self, verdict):
        return bugle.CopyDesk({"high": 51}, self.report, [], BIRD, ask=lambda c: verdict)

    def test_clean_copy_passes(self):
        desk = self.desk({"weather": {"verdict": "pass", "errors": []},
                          "bird": {"verdict": "pass", "errors": []}})
        clean, review = bugle.run_copy_desk(self.ed, desk, last_chance=False)
        self.assertEqual(review["status"], "passed")
        self.assertIs(clean, self.ed)

    def test_flagged_copy_is_sent_back_for_a_rewrite(self):
        desk = self.desk({"weather": {"verdict": "fail",
                                      "errors": ["'eve of Thanksgiving' is wrong; today is Tuesday."]},
                          "bird": {"verdict": "pass", "errors": []}})
        with self.assertRaisesRegex(ValueError, "copy desk flagged"):
            bugle.run_copy_desk(self.ed, desk, last_chance=False)
        self.assertIn("Fix exactly those claims",
                      bugle.rejection_note(ValueError("copy desk flagged: x")))

    def test_last_chance_swaps_in_sourced_text(self):
        desk = self.desk({"weather": {"verdict": "fail", "errors": ["holiday"]},
                          "bird": {"verdict": "fail", "errors": ["'crow-sized' unsupported"]}})
        clean, review = bugle.run_copy_desk(self.ed, desk, last_chance=True)
        self.assertEqual(review["replaced"], ["weather", "bird"])
        self.assertEqual(clean["weather"]["editorial"], self.report.editorial)
        self.assertEqual(clean["grounds"], "The common raven is a large all-black passerine bird.")
        self.assertIn("Thanksgiving", self.ed["weather"]["verdict"])  # original untouched

    def test_narrated_passes_are_not_errors(self):
        desk = self.desk({"weather": {"verdict": "fail", "errors": ["No errors found."]},
                          "bird": {"verdict": "pass", "errors": ["'grayish' is supported."]}})
        clean, review = bugle.run_copy_desk(self.ed, desk, last_chance=False)
        self.assertEqual(review["status"], "passed")

    def test_long_caption_is_repaired_on_the_last_chance(self):
        desk = self.desk({"weather": {"verdict": "pass"}, "bird": {"verdict": "pass"}})
        long = dict(self.ed, grounds=" ".join(["word"] * 36))
        with self.assertRaisesRegex(ValueError, "caption runs 36 words"):
            bugle.run_copy_desk(long, desk, last_chance=False)
        clean, review = bugle.run_copy_desk(long, desk, last_chance=True)
        self.assertEqual(review["replaced"], ["bird"])

    def test_desk_outage_does_not_block_the_paper(self):
        def down(content):
            raise RuntimeError("API down")
        desk = bugle.CopyDesk({"high": 51}, self.report, [], BIRD, ask=down)
        clean, review = bugle.run_copy_desk(self.ed, desk, last_chance=False)
        self.assertEqual(review["status"], "unavailable")

    def test_copy_prompt_carries_the_reference_and_date(self):
        seen = {}
        def ask(content):
            seen["text"] = content[0]["text"]
            return {"weather": {"verdict": "pass"}, "bird": {"verdict": "pass"}}
        copy_desk.check_copy(self.ed, self.desk(None).facts, ask)
        self.assertIn("most widely distributed of all corvids", seen["text"])
        self.assertIn(bugle.DATE_LINE, seen["text"])


if __name__ == "__main__":
    unittest.main()
