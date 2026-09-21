"""Choosing between two Ace lists with different terms."""

import unittest
from decimal import Decimal

from bw.models import SupplierItem
from bw.pricing import PricingEngine
from bw.sourcing import choose_sources, compare, identity
from support import fixed_engine


def item(supplier, sku, title, cost, qty=10, barcode=None, brand="Lattafa", size_ml=100):
    return SupplierItem(
        supplier=supplier, supplier_sku=sku, title=title, brand=brand,
        cost=cost, qty=qty, barcode=barcode, size_ml=size_ml, size_label=f"{size_ml}ml",
        concentration="EDP", gender="Man",
    )


class IdentityTests(unittest.TestCase):
    def test_the_same_barcode_is_the_same_item(self):
        a = item("ace_dropship", "D1", "Asad EDP 100ml", "22.00", barcode="6291108730324")
        b = item("ace_wholesale", "W1", "LATTAFA ASAD (M) EDP 3.4oz", "26.00",
                 barcode="6291108730324")
        self.assertEqual(identity(a), identity(b))

    def test_without_barcodes_the_attributes_decide(self):
        a = item("ace_dropship", "D1", "Asad EDP 100ml", "22.00")
        b = item("ace_wholesale", "W1", "Lattafa Asad EDP 100ml", "26.00")
        self.assertEqual(identity(a), identity(b))

    def test_different_sizes_are_different_items(self):
        a = item("ace_dropship", "D1", "Asad EDP 100ml", "22.00", size_ml=100)
        b = item("ace_dropship", "D2", "Asad EDP 50ml", "14.00", size_ml=50)
        self.assertNotEqual(identity(a), identity(b))


class SourceChoiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = fixed_engine()

    def test_the_flat_fee_can_beat_a_lower_sticker_price(self):
        # $22 dropship (carrying the $15) against $26 wholesale (carrying none).
        # The cheaper sticker is the worse deal.
        feeds = {
            "ace": [item("ace", "D1", "Asad EDP 100ml", "22.00")],
            "onhand": [item("onhand", "W1", "Asad EDP 100ml", "26.00")],
        }
        chosen = choose_sources(feeds, self.engine)
        self.assertEqual(len(chosen), 1)
        sourced = next(iter(chosen.values()))
        self.assertEqual(sourced.supplier, "onhand")
        self.assertGreater(sourced.saving, Decimal("0"))

    def test_a_big_enough_gap_still_favours_the_dropship_list(self):
        feeds = {
            "ace": [item("ace", "D1", "Asad EDP 100ml", "10.00")],
            "onhand": [item("onhand", "W1", "Asad EDP 100ml", "26.00")],
        }
        sourced = next(iter(choose_sources(feeds, self.engine).values()))
        self.assertEqual(sourced.supplier, "ace")

    def test_an_item_on_only_one_list_is_still_sourced(self):
        feeds = {
            "ace": [item("ace", "D1", "Asad EDP 100ml", "22.00")],
            "onhand": [item("onhand", "W9", "Khamrah EDP 100ml", "30.00")],
        }
        chosen = choose_sources(feeds, self.engine)
        self.assertEqual(len(chosen), 2)
        self.assertTrue(all(not s.alternatives for s in chosen.values()))

    def test_out_of_stock_sources_are_skipped(self):
        feeds = {
            "ace": [item("ace", "D1", "Asad EDP 100ml", "10.00", qty=0)],
            "onhand": [item("onhand", "W1", "Asad EDP 100ml", "26.00", qty=4)],
        }
        sourced = next(iter(choose_sources(feeds, self.engine).values()))
        self.assertEqual(sourced.supplier, "onhand",
                         "the cheaper list has none in stock")

    def test_out_of_stock_can_be_included_deliberately(self):
        feeds = {"ace": [item("ace", "D1", "Asad EDP 100ml", "10.00", qty=0)]}
        self.assertEqual(len(choose_sources(feeds, self.engine, in_stock_only=False)), 1)

    def test_items_with_no_cost_are_never_sourced(self):
        feeds = {"ace": [item("ace", "D1", "Asad EDP 100ml", None)]}
        self.assertEqual(choose_sources(feeds, self.engine), {})

    def test_stock_breaks_a_tie(self):
        # Identical terms and identical cost: nothing to choose but stock.
        feeds = {
            "twin_a": [item("twin_a", "D1", "Asad EDP 100ml", "22.00", qty=2)],
            "twin_b": [item("twin_b", "D2", "Asad EDP 100ml", "22.00", qty=99)],
        }
        sourced = next(iter(choose_sources(feeds, self.engine).values()))
        self.assertEqual(sourced.item.qty, 99)


class CompareTests(unittest.TestCase):
    def test_summary_counts_overlap_and_saving(self):
        feeds = {
            "ace": [
                item("ace", "D1", "Asad EDP 100ml", "22.00"),
                item("ace", "D2", "Khamrah EDP 100ml", "26.00"),
            ],
            "onhand": [
                item("onhand", "W1", "Asad EDP 100ml", "26.00"),
            ],
        }
        result = compare(feeds)
        self.assertEqual(result["distinct_items"], 2)
        self.assertEqual(result["on_more_than_one_list"], 1)
        self.assertEqual(result["feed_sizes"], {"ace": 2, "onhand": 1})
        self.assertGreater(result["total_saving"], Decimal("0"))
        self.assertEqual(sum(result["wins"].values()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TieTests(unittest.TestCase):
    """Above the fee taper the two sets of terms converge."""

    def test_an_expensive_item_prices_the_same_either_way(self):
        # Over $150 the order fee is treated as covered, so a dropship list and
        # a stocked one land on the same floor.
        feeds = {
            "ace": [item("ace", "D1", "Expensive EDP 100ml", "200.00")],
            "ace_stocked": [item("ace_stocked", "S1", "Expensive EDP 100ml", "200.00")],
        }
        sourced = next(iter(choose_sources(feeds).values()))
        self.assertTrue(sourced.tied)
        self.assertEqual(sourced.saving, Decimal("0"))

    def test_ties_are_not_credited_to_either_list(self):
        feeds = {
            "ace": [item("ace", "D1", "Expensive EDP 100ml", "200.00")],
            "ace_stocked": [item("ace_stocked", "S1", "Expensive EDP 100ml", "200.00")],
        }
        result = compare(feeds)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(sum(result["wins"].values()), 0)

    def test_a_cheap_item_is_not_a_tie(self):
        feeds = {
            "ace": [item("ace", "D1", "Cheap EDT 35ml", "5.00")],
            "ace_stocked": [item("ace_stocked", "S1", "Cheap EDT 35ml", "5.00")],
        }
        sourced = next(iter(choose_sources(feeds).values()))
        self.assertFalse(sourced.tied)
        self.assertEqual(sourced.supplier, "ace_stocked", "no order fee wins")
