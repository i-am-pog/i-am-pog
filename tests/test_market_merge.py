"""Pricing against several competitors at once."""

import unittest
from decimal import Decimal

from bw.market.shopify_store import MarketEntry, MarketIndex


def entry(brand, title, size, price):
    return MarketEntry(brand=brand, title=title, size_ml=size, price=Decimal(price))


class MergingStores(unittest.TestCase):
    """One store is not a market -- catalogues barely overlap."""

    def test_a_bottle_only_the_second_store_carries_is_found(self):
        a = [entry("Afnan", "Afnan Supremacy Noir", 100, "41.00")]
        b = [entry("Afnan", "Afnan 9PM Night Out", 100, "59.00")]
        merged = MarketIndex(a + b)
        self.assertEqual(merged.lookup("Afnan", "Afnan 9PM Night Out", 100),
                         Decimal("59.00"))

    def test_the_cheaper_of_two_stores_wins(self):
        # It is the price we actually have to beat.
        merged = MarketIndex([
            entry("Afnan", "Afnan Supremacy Noir", 100, "48.00"),
            entry("Afnan", "Afnan Supremacy Noir", 100, "41.00"),
        ])
        self.assertEqual(merged.lookup("Afnan", "Afnan Supremacy Noir", 100),
                         Decimal("41.00"))

    def test_the_wrong_size_is_still_refused_after_merging(self):
        merged = MarketIndex([entry("Afnan", "Afnan Supremacy Noir", 100, "41.00")])
        self.assertIsNone(merged.lookup("Afnan", "Afnan Supremacy Noir", 50))


if __name__ == "__main__":
    unittest.main()
