"""How supplier rows become listings."""

import unittest
from decimal import Decimal

from bw.listing import build_product_input
from bw.normalize import clean_product_name
from bw.models import SupplierItem
from bw.pricing import PriceQuote


class TestersGetTheirOwnListing(unittest.TestCase):
    """A tester and the boxed bottle must not collide on title or handle."""

    def _product(self, tester):
        item = SupplierItem(
            supplier="ace", supplier_sku="T1" if tester else "B1",
            brand="Scentstory", title="24 Elixir Platinum", concentration="EDP",
            gender="Man", size_ml=100, size_label="100ml", qty=3,
            cost=Decimal("30.00"), tester=tester,
        )
        quote = PriceQuote(
            price=Decimal("59.95"), floor_price=Decimal("45.00"),
            allocated_fee=Decimal("5.00"), cost=Decimal("30.00"),
            min_margin=Decimal("0.25"), basis="market",
        )
        return build_product_input([item], {item.supplier_sku: quote}, "gid://loc/1")

    def test_the_tester_says_so_in_the_title(self):
        self.assertIn("(Tester)", self._product(True)["title"])
        self.assertNotIn("Tester", self._product(False)["title"])

    def test_they_do_not_share_a_handle(self):
        self.assertNotEqual(self._product(True)["handle"],
                            self._product(False)["handle"])


class TheBrandIsNotSaidTwice(unittest.TestCase):
    """The feed's spelling of a brand is not always ours."""

    def test_an_ampersand_in_our_brand_still_matches_the_feed(self):
        # This shipped: "Abercrombie & Fitch Abercrombie Fitch First Instinct".
        self.assertEqual(
            clean_product_name("Abercrombie Fitch First Instinct",
                               "Abercrombie & Fitch"),
            "First Instinct")

    def test_the_brand_spelled_out_with_and(self):
        self.assertEqual(
            clean_product_name("Abercrombie and Fitch Authentic",
                               "Abercrombie & Fitch"),
            "Authentic")

    def test_an_exact_brand_is_still_stripped(self):
        self.assertEqual(clean_product_name("Versace Eros", "Versace"), "Eros")

    def test_a_title_that_is_only_the_brand_keeps_its_name(self):
        # Stripping everything would leave a product with no name at all.
        self.assertTrue(clean_product_name("Versace", "Versace"))

    def test_a_name_that_merely_starts_like_the_brand_is_left_alone(self):
        self.assertEqual(clean_product_name("Boss Bottled", "Boss Orange"),
                         "Boss Bottled")


if __name__ == "__main__":
    unittest.main()
