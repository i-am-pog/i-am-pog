"""The PDF price-list parser, against the awkward real rows."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from parse_pricelist import join_wrapped, parse_lines, split_brand, strip_type

# Verbatim lines from a real supplier price list.
LINES = [
    "SKU Barcode Brand Item Name Type Concentration Gender Size",
    "4711LMM-170B 4011700747740 4711 4711 Acqua Colonia Lychee and White Mint EDC M 170ml Boxed \\*\\*RARE\\*\\* Boxed Eau De Cologne Man 170ml 9 $29.89",
    "ARMMLW-100B 6085010093574 Armaf Armaf Momento Lace W 100ml Boxed Boxed Eau De Parfum Woman 100ml 39 $23.22",
    "ATRCOLFDSM-100T ATC09751248 Attar Collection Tester - Attar Collection Fleur De Santal EDP M 100ml Tester Tester Eau De Parfum Man 100ml 7 $75.56",
    "ARMCDNUELIXM-105B 6294015163513 Armaf Armaf Club De Nuit Urban Man (Elixir Edition) M 105ml Boxed Boxed Elixir Man 105ml 170 $46.66",
    "BOUW-100T 3386460036771 Boucheron Tester - Boucheron EDT Edition W 100ml Tester RARE Tester Eau de Toilette Woman 100ml 16 $34.48",
    "AGROPOMAFEDW-100B NoBarcode Annick Goutal Annick Goutal Rose Pompon (Alcohol Free Edition) W 100ml Boxed RARE Boxed Eau Sans Alcohol Woman 100ml 5 $58.33",
    "AFN9PMRBLM-100S NoBarcode Afnan Set - Afnan 9PM Rebel EDP M 100ml Gift Set Gift Set Eau De Parfum Man 100ml 176 $48.88",
]

WRAPPED = [
    "ATKTNM-100B 8002135157996 Atkinsons Atkinson Tulipe Noir M EDP 100ml Boxed RARE",
    "Boxed Eau De Parfum Man 100ml 6 $130.00",
]


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.unmatched = parse_lines(LINES)
        self.by_sku = {r["sku"]: r for r in self.rows}

    def test_every_real_row_parses(self):
        self.assertEqual(len(self.rows), 7, f"unmatched: {self.unmatched}")

    def test_pulls_the_structured_tail(self):
        row = self.by_sku["4711LMM-170B"]
        self.assertEqual(row["barcode"], "4011700747740")
        self.assertEqual(row["cost"], "29.89")
        self.assertEqual(row["qty"], "9")
        self.assertEqual(row["size"], "170ml")
        self.assertEqual(row["gender"], "Man")
        self.assertEqual(row["concentration"], "Eau De Cologne")

    def test_splits_a_repeated_brand_off_the_item_name(self):
        row = self.by_sku["ARMMLW-100B"]
        self.assertEqual(row["brand"], "Armaf")
        self.assertTrue(row["title"].startswith("Armaf Momento Lace"))
        self.assertEqual(row["type"], "Boxed")

    def test_multi_word_brands(self):
        self.assertEqual(self.by_sku["ATRCOLFDSM-100T"]["brand"], "Attar Collection")
        self.assertEqual(self.by_sku["AGROPOMAFEDW-100B"]["brand"], "Annick Goutal")

    def test_an_internal_code_in_the_barcode_column_is_not_kept_as_a_barcode(self):
        row = self.by_sku["ATRCOLFDSM-100T"]
        self.assertEqual(row["barcode"], "", "ATC09751248 is not a barcode")
        self.assertEqual(row["type"], "Tester")

    def test_nobarcode_is_empty_not_the_word(self):
        self.assertEqual(self.by_sku["AGROPOMAFEDW-100B"]["barcode"], "")

    def test_unusual_concentrations(self):
        self.assertEqual(self.by_sku["ARMCDNUELIXM-105B"]["concentration"], "Elixir")
        self.assertEqual(self.by_sku["AGROPOMAFEDW-100B"]["concentration"], "Eau Sans Alcohol")

    def test_lowercase_de_still_matches(self):
        self.assertEqual(self.by_sku["BOUW-100T"]["concentration"], "Eau de Toilette")

    def test_gift_sets(self):
        row = self.by_sku["AFN9PMRBLM-100S"]
        self.assertEqual(row["brand"], "Afnan")
        self.assertEqual(row["type"], "Gift Set")
        self.assertEqual(row["qty"], "176")

    def test_header_row_is_skipped(self):
        self.assertNotIn("SKU", self.by_sku)


class WrappedRowTests(unittest.TestCase):
    def test_a_row_split_across_two_lines_is_rejoined(self):
        joined = join_wrapped(WRAPPED)
        self.assertEqual(len(joined), 1)
        rows, unmatched = parse_lines(WRAPPED)
        self.assertEqual(len(rows), 1, f"unmatched: {unmatched}")
        self.assertEqual(rows[0]["cost"], "130.00")
        self.assertEqual(rows[0]["brand"], "Atkinsons")


class BrandSplitTests(unittest.TestCase):
    def test_repetition_is_preferred(self):
        self.assertEqual(split_brand("Armaf Armaf Momento Lace"), ("Armaf", "Armaf Momento Lace"))

    def test_vocabulary_catches_what_repetition_misses(self):
        brand, item = split_brand("Afnan Set - Afnan 9PM Rebel", {"afnan"})
        self.assertEqual(brand, "Afnan")
        self.assertEqual(item, "Set - Afnan 9PM Rebel")

    def test_falls_back_to_the_first_token(self):
        brand, _ = split_brand("Unheardof Something Or Other")
        self.assertEqual(brand, "Unheardof")

    def test_empty_input_is_survivable(self):
        self.assertEqual(split_brand(""), ("", ""))


class TypeTests(unittest.TestCase):
    def test_packaging_comes_off_the_end(self):
        self.assertEqual(strip_type("Momento Lace W 100ml Boxed"), ("Momento Lace W 100ml", "Boxed"))
        self.assertEqual(strip_type("Something Gift Set"), ("Something", "Gift Set"))

    def test_item_with_no_packaging_word_is_untouched(self):
        self.assertEqual(strip_type("Plain Item"), ("Plain Item", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
