import json
import unittest
import datetime as dt
from unittest.mock import Mock, patch

import bugle_action as bugle


class EditorialContractTests(unittest.TestCase):
    def sample(self):
        return {
            "quote": {"text": "A short line.", "attr": "A Writer"},
            "weather": {"verdict": "Mostly usable",
                        "editorial": "High 72°, with a 10 percent rain chance."},
            "headlines": {"local": [{"head": "Local news", "line": "A factual line."}]},
            "grounds": "The beds are moving through summer.",
        }

    def test_normalize_accepts_rendering_contract(self):
        clean = bugle.normalize_editorial(self.sample())
        self.assertEqual(clean["weather"]["verdict"], "Mostly usable")

    def test_normalize_rejects_empty_headline_list(self):
        data = self.sample()
        data["headlines"]["local"] = []
        with self.assertRaises(ValueError):
            bugle.normalize_editorial(data)

    def test_bird_caption_length_is_enforced(self):
        data = self.sample()
        data["grounds"] = " ".join(["word"] * (bugle.BIRD_NOTE_HARD_LIMIT + 1))
        with self.assertRaisesRegex(ValueError, "bird caption runs"):
            bugle.validate_novelty(data, [], bird_mode=True)

    def test_bird_caption_may_use_numbers(self):
        data = self.sample()
        data["grounds"] = "Listen for two clear whistled notes from the treetops."
        self.assertEqual(bugle.validate_novelty(data, [], bird_mode=True), data)

    def test_editor_json_ignores_trailing_remarks(self):
        text = '```json\n{"a": 1}\n```\nHope this helps.'
        self.assertEqual(bugle.parse_editor_json(text), {"a": 1})

    def test_novelty_rejects_unsupported_grounds_comparison(self):
        data = self.sample()
        data["grounds"] = "Sunrise is six minutes later than at the solstice."
        with self.assertRaisesRegex(ValueError, "unsupported numeric"):
            bugle.validate_novelty(data, [])
        data["grounds"] = "Sunrise arrives noticeably later than midsummer."
        with self.assertRaisesRegex(ValueError, "unsupported numeric"):
            bugle.validate_novelty(data, [])

    def test_curated_quote_avoids_recent_author_and_text(self):
        recent = [
            {"quote": dict(bugle.CURATED_QUOTES[0])},
            {"quote": dict(bugle.CURATED_QUOTES[1])},
        ]
        selected = bugle.choose_quote(recent)
        self.assertNotIn(selected, [item["quote"] for item in recent])
        self.assertNotIn(bugle.quote_author_key(selected["attr"]), {
            bugle.quote_author_key(item["quote"]["attr"]) for item in recent
        })

    def test_repeated_district_story_is_filtered(self):
        raw = {"local": {"items": [{
            "title": "Harriet Tubman Elementary reopens after modernization",
            "age_hours": 2,
        }], "failed": []}}
        recent = [{"headlines": [{
            "head": "Tubman School Reborn",
            "line": "Harriet Tubman Elementary reopened after modernization.",
        }]}]
        self.assertEqual(bugle.prepare_headlines(raw, recent)["local"]["items"], [])

    def test_weekend_filter_removes_old_feed_item(self):
        raw = {"local": {"items": [{"title": "Old local item", "age_hours": 48}],
                      "failed": []}}
        sunday = dt.datetime(2026, 8, 30, 5, tzinfo=bugle.TZ)
        with patch.object(bugle, "NOW", sunday):
            result = bugle.prepare_headlines(raw, [])
        self.assertEqual(result["local"]["items"], [])

    def test_extracts_responses_api_output_text(self):
        body = {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"ok": true}'}
        ]}]}
        self.assertEqual(bugle.openai_output_text(body), '{"ok": true}')

    def test_http_error_detail_reports_only_type_and_code(self):
        response = Mock()
        response.json.return_value = {"error": {
            "message": "Sensitive provider detail",
            "type": "insufficient_quota",
            "code": "insufficient_quota",
        }}
        self.assertEqual(
            bugle.http_error_detail(response),
            " (type=insufficient_quota, code=insufficient_quota)",
        )

    def test_openai_uses_responses_api_with_strict_schema(self):
        sample = self.sample()
        body = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(sample)}
        ]}], "usage": {"input_tokens": 1000, "output_tokens": 500}}
        old_key = bugle.OPENAI_API_KEY
        bugle.OPENAI_API_KEY = "test-key"
        try:
            with patch.object(bugle, "post_with_backoff", return_value=(body, 1.25)) as post:
                result, diagnostics = bugle.ask_openai("prompt")
        finally:
            bugle.OPENAI_API_KEY = old_key
        payload = post.call_args.args[3]
        self.assertEqual(payload["model"], "gpt-5.6-luna")
        self.assertEqual(payload["reasoning"]["effort"], "low")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(result["quote"]["attr"], "A Writer")
        self.assertEqual(diagnostics["estimated_cost_usd"], 0.0008)

    def test_emergency_editorial_handles_weather_outage(self):
        result = bugle.emergency_editorial(
            None, {"local": {"items": [], "failed": ["local wire"]}}, None
        )
        self.assertEqual(result["weather"]["verdict"], "Weather wire down")
        self.assertEqual(result["headlines"]["local"][0]["head"], "Wire down")

    def test_weather_wire_down_means_no_grade(self):
        self.assertIsNone(bugle.grade_weather({"high": "—", "hours": []}, []))

    def test_editor_cannot_set_the_weather_grade(self):
        self.assertNotIn("grade", bugle.EDITORIAL_SCHEMA["properties"]["weather"]["properties"])

    def test_clock_is_twelve_hour(self):
        self.assertEqual(bugle.clock("2026-11-24T06:58"), "6:58 am")
        self.assertEqual(bugle.clock("2026-11-24T16:48"), "4:48 pm")


if __name__ == "__main__":
    unittest.main()
