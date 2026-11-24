import io
import unittest

from PIL import Image

import bird_desk


def sighting(name, km, code=None):
    return dict(common_name=name, scientific_name=f"Genus {name.split()[-1].lower()}",
                species_code=code or name[:6].lower(), observed="2026-11-22 08:14",
                place="Rock Creek Park", km=km)


class BirdDeskTests(unittest.TestCase):
    def test_hotspot_names_are_shortened(self):
        self.assertEqual(bird_desk.short_place("Rock Creek Park--Picnic Grove 10"),
                         "Rock Creek Park")

    def test_recently_featured_birds_are_skipped(self):
        sightings = [sighting("Hermit Thrush", 1), sighting("Carolina Wren", 1)]
        history = [{"bird": "Hermit Thrush", "grounds": ""}]
        order = bird_desk.candidate_order(sightings, history, "2026-11-24")
        self.assertEqual([s["common_name"] for s in order], ["Carolina Wren"])

    def test_birds_named_in_old_grounds_copy_are_skipped(self):
        sightings = [sighting("Cedar Waxwing", 1), sighting("Blue Jay", 1)]
        history = [{"grounds": "A Cedar Waxwing has been reported nearby this week."}]
        order = bird_desk.candidate_order(sightings, history, "2026-11-24")
        self.assertEqual([s["common_name"] for s in order], ["Blue Jay"])

    def test_nearby_birds_come_first_and_choice_is_stable_for_the_day(self):
        sightings = [sighting("Far Bird", 7), sighting("Near Bird", 1), sighting("Close Bird", 2)]
        first = bird_desk.candidate_order(sightings, [], "2026-11-24")
        again = bird_desk.candidate_order(list(reversed(sightings)), [], "2026-11-24")
        self.assertEqual(first, again)
        self.assertEqual(first[-1]["common_name"], "Far Bird")

    def test_photo_is_cropped_and_grayscale(self):
        buf = io.BytesIO()
        Image.new("RGB", (1200, 800), (120, 160, 90)).save(buf, "JPEG")
        out = Image.open(io.BytesIO(bird_desk.prepare_photo(buf.getvalue(), (634, 641))))
        self.assertEqual(out.size, (634, 641))
        self.assertEqual(out.mode, "L")

    def test_commons_filename_handles_thumbnail_urls(self):
        thumb = ("https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/"
                 "Corvus_corax_clarionensis%2C_Point_Reyes.jpg/3840px-Corvus_corax_clarionensis"
                 "%2C_Point_Reyes.jpg?utm_source=en.wikipedia.org&utm_campaign=api")
        self.assertEqual(bird_desk.commons_filename(thumb),
                         "Corvus_corax_clarionensis,_Point_Reyes.jpg")
        self.assertEqual(bird_desk.commons_filename(
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/Hermit_Thrush.jpg"),
            "Hermit_Thrush.jpg")

    def test_credit_markup_is_cleaned(self):
        self.assertEqual(bird_desk.clean_markup('<a href="x">Jane&nbsp;Doe</a>'), "Jane Doe")


if __name__ == "__main__":
    unittest.main()


class ReferenceTests(unittest.TestCase):
    def test_reference_keeps_lead_and_field_sections_only(self):
        article = ("The dark-eyed junco is a species of junco.\n\n"
                   "== Taxonomy ==\nSystematics are unresolved.\n\n"
                   "== Description ==\nAdults have white outer tail feathers.\n\n"
                   "=== Subspecies ===\nMany forms.\n\n"
                   "== Behaviour ==\nThey forage on the ground.\n\n"
                   "== References ==\nCitation.")
        ref = bird_desk.reference_from_article(article)
        self.assertIn("white outer tail feathers", ref)
        self.assertIn("forage on the ground", ref)
        self.assertNotIn("Systematics", ref)
        self.assertNotIn("Citation", ref)
        self.assertTrue(ref.startswith("The dark-eyed junco"))
