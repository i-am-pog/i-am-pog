"""Nothing broken or bloated reaches a product page."""

import unittest

from bw.descriptions import MAX_WORDS, problems, require_clean
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

    def test_empty_and_clone_wording_are_refused(self):
        self.assertIn("empty", problems(""))
        self.assertTrue(problems("<p>A clone of a designer scent.</p>"))
        self.assertEqual(problems("<p>Woody, with a slightly darker twist.</p>"), [])

    def test_the_csv_export_will_not_write_a_bad_description(self):
        product = {"handle": "x", "title": "X", "descriptionHtml": "<p>???? ????</p>",
                   "variants": [{"price": "10.00"}]}
        with self.assertRaises(ValueError):
            product_rows(product)


if __name__ == "__main__":
    unittest.main()
