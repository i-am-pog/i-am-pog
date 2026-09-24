"""Duplicate supplier listings for one variant of ours."""

import unittest

from bw.cli import keep_cheapest_per_variant


def row(variant_id: str, cost: str, price: str) -> dict:
    return {"variant_id": variant_id, "cost": cost, "new_price": price}


class KeepCheapestPerVariant(unittest.TestCase):
    def test_untouched_when_every_row_is_its_own_variant(self):
        rows = [row("v1", "25.00", "44.99"), row("v2", "19.36", "34.99")]
        kept, duplicates = keep_cheapest_per_variant(rows)
        self.assertEqual(duplicates, 0)
        self.assertEqual(kept, rows)

    def test_second_listing_of_the_same_bottle_loses_to_the_cheaper_cost(self):
        # Ace really does quote Alyssa Ashley at both of these.
        rows = [row("v1", "25.00", "44.99"), row("v1", "19.36", "34.99")]
        kept, duplicates = keep_cheapest_per_variant(rows)
        self.assertEqual(duplicates, 1)
        self.assertEqual([r["cost"] for r in kept], ["19.36"])

    def test_cheaper_listing_first_still_wins(self):
        rows = [row("v1", "19.36", "34.99"), row("v1", "25.00", "44.99")]
        kept, duplicates = keep_cheapest_per_variant(rows)
        self.assertEqual(duplicates, 1)
        self.assertEqual([r["cost"] for r in kept], ["19.36"])

    def test_costs_compare_as_money_not_as_text(self):
        # "9.99" sorts after "10.00" as a string; it is cheaper as money.
        rows = [row("v1", "10.00", "24.99"), row("v1", "9.99", "22.99")]
        kept, _ = keep_cheapest_per_variant(rows)
        self.assertEqual([r["cost"] for r in kept], ["9.99"])

    def test_three_listings_collapse_to_one(self):
        rows = [row("v1", "25.00", "1"), row("v1", "22.00", "2"), row("v1", "30.00", "3")]
        kept, duplicates = keep_cheapest_per_variant(rows)
        self.assertEqual(duplicates, 2)
        self.assertEqual([r["cost"] for r in kept], ["22.00"])

    def test_first_appearance_order_is_kept(self):
        rows = [row("v1", "25.00", "1"), row("v2", "12.00", "2"), row("v1", "19.00", "3")]
        kept, _ = keep_cheapest_per_variant(rows)
        self.assertEqual([r["variant_id"] for r in kept], ["v1", "v2"])


if __name__ == "__main__":
    unittest.main()
