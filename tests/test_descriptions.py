"""Nothing broken or bloated reaches a product page."""

import unittest

from bw.descriptions import MAX_WORDS, problems, require_clean, standard_problems
from bw.export import product_rows

GOOD = ("<p><strong>Joop Femme</strong> is an eau de toilette for women.</p>"
        "<ul><li><strong>Top notes:</strong> orange blossom</li></ul>"
        "<p>100% authentic. Ships from our warehouse in Oakville, Ontario.</p>")


class DescriptionGate(unittest.TestCase):
    def test_a_normal_description_passes(self):
        self.assertEqual(problems(GOOD), [])
        self.assertEqual(require_clean(GOOD), GOOD)

    def test_the_question_marks_from_the_old_import_are_refused(self):
        bad = "<p>???????? ?????????????: Alcohol and fragrant oils make Chloe perfumes.</p>"
        self.assertTrue(any("question-mark" in p for p in problems(bad)))
        with self.assertRaises(ValueError):
            require_clean(bad, "Chloe")

    def test_fancy_unicode_letters_are_refused_before_they_turn_into_question_marks(self):
        self.assertTrue(any("fancy" in p for p in problems("<p>\U0001D5D9\U0001D5FF\U0001D5EE notes</p>")))

    def test_garbled_encoding_is_refused(self):
        self.assertTrue(any("garbled" in p for p in problems("<p>LattafaÃ¢ Dark Door</p>")))
        self.assertTrue(any("garbled" in p for p in problems("<p>Itâ€™s fresh</p>")))

    def test_accented_words_are_fine(self):
        self.assertEqual(problems("<p>Eau de Cologne with crème brûlée and néroli.</p>"), [])

    def test_filler_past_the_limit_is_refused(self):
        long = "<p>" + "word " * (MAX_WORDS + 1) + "</p>"
        self.assertTrue(any("too long" in p for p in problems(long)))

    def test_text_pasted_from_another_site_is_refused(self):
        pasted = '<p><a href="https://brandswarehouse.ca/"><b>Flowerbomb</b><span></span>by ViktorandRolf</a></p>'
        self.assertTrue(any("pasted" in p for p in problems(pasted)))

    def test_copy_in_the_makers_voice_is_refused(self):
        cool_water = "<p>LONG LAST SMELLING EFFECT: Our perfume concentration offers an impressive balm projection.</p>"
        self.assertTrue(any("as if we make" in p for p in problems(cool_water)))
        self.assertTrue(problems("<p>Authenticity Promise</p>"))

    def test_the_my_perfumes_house_is_a_brand_not_the_store_talking(self):
        self.assertEqual(problems("<p>My Perfumes Arabiyat Oud Al Layl is a rich oud.</p>"), [])

    def test_empty_and_clone_wording_are_refused(self):
        self.assertIn("empty", problems(""))
        self.assertTrue(problems("<p>A clone of a designer scent.</p>"))
        self.assertEqual(problems("<p>Woody, with a slightly darker twist.</p>"), [])

    def test_the_csv_export_will_not_write_a_bad_description(self):
        product = {"handle": "x", "title": "X", "descriptionHtml": "<p>???? ????</p>",
                   "variants": [{"price": "10.00"}]}
        with self.assertRaises(ValueError):
            product_rows(product)


ETERNITY = ("<p><strong>Calvin Klein Eternity Cologne</strong> is a classic men's fragrance from Calvin Klein "
            "with a fresh, green and woody character. Lavender, mandarin, bergamot and lemon open crisply, a "
            "herbal floral heart of sage, basil, geranium and jasmine follows, and sandalwood, amber, vetiver "
            "and rosewood give it lasting depth. A timeless, clean scent for work and everyday wear.</p><ul>\n"
            "<li>\n<strong>Top notes:</strong> lavender, mandarin, bergamot, lemon</li>\n"
            "<li>\n<strong>Heart notes:</strong> sage, basil, geranium, jasmine, lily, juniper berry</li>\n"
            "<li>\n<strong>Base notes:</strong> sandalwood, amber, musk, vetiver, rosewood</li>\n"
            "<li>\n<strong>Scent family:</strong> aromatic foug\u00e8re</li>\n"
            "<li>\n<strong>Best for:</strong> office, everyday wear, spring and summer</li>\n"
            "<li>\n<strong>Concentration:</strong> Eau de Toilette: lighter and fresh, easy to wear every day</li>\n</ul>")


class HouseFormat(unittest.TestCase):
    def test_the_eternity_page_is_the_standard(self):
        self.assertEqual(standard_problems(ETERNITY), [])

    def test_the_shipping_line_is_no_longer_part_of_it(self):
        with_footer = ETERNITY + "<p>100% authentic. Ships from our warehouse in Oakville, Ontario.</p>"
        self.assertTrue(standard_problems(with_footer))

    def test_a_missing_line_is_caught(self):
        self.assertTrue(standard_problems(ETERNITY.replace("Best for", "Good for")))

    def test_a_bare_one_liner_is_caught(self):
        self.assertTrue(standard_problems("<p><strong>Hermes Amazone</strong>.</p>"))


if __name__ == "__main__":
    unittest.main()
