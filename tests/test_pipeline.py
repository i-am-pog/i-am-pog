"""End to end: a supplier feed in, Shopify product payloads out."""

import json
import unittest
from decimal import Decimal
from pathlib import Path

from bw.listing import build_product_input, group_items, missing_images
from bw.market import MarketIndex
from bw.market.shopify_store import MarketEntry
from bw.match import CatalogIndex, partition
from bw.models import CatalogVariant
from bw.pricing import PricingEngine
from bw.suppliers import get_adapter, load_config
from support import fixed_engine

FIXTURES = Path(__file__).parent / "fixtures"
LOCATION = "gid://shopify/Location/52596932776"


def load_catalog():
    return [CatalogVariant(**json.loads(line))
            for line in (FIXTURES / "catalog.jsonl").read_text().splitlines() if line.strip()]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        config = load_config(FIXTURES / "suppliers.yaml")
        self.items = get_adapter("ace", config=config).fetch()
        self.index = CatalogIndex(load_catalog())
        self.engine = fixed_engine()

    def test_feed_parses(self):
        self.assertEqual(len(self.items), 10)
        asad = next(i for i in self.items if i.supplier_sku == "I0091001")
        self.assertEqual(asad.brand, "Lattafa")
        self.assertEqual(asad.size_ml, 100)
        self.assertEqual(asad.cost, Decimal("22.50"))
        self.assertEqual(asad.qty, 120)
        self.assertEqual(asad.gender, "Man")
        self.assertEqual(asad.concentration, "EDP")

    def test_item_without_a_cost_is_not_sellable(self):
        broken = next(i for i in self.items if i.supplier_sku == "I0071101")
        self.assertFalse(broken.sellable)

    def test_items_we_already_carry_are_not_re_added(self):
        buckets = partition([i for i in self.items if i.sellable], self.index)
        existing = {r.item.supplier_sku for r in buckets["existing"]}
        # Both 212 sizes are already on the site, and 212 Heroes is already
        # sourced from Ace.
        self.assertIn("I0079777", existing)
        self.assertIn("I0082512", existing)
        self.assertIn("I0098516", existing)

    def test_genuinely_new_items_are_found(self):
        buckets = partition([i for i in self.items if i.sellable], self.index)
        new = {r.item.supplier_sku for r in buckets["new"]}
        self.assertIn("I0091001", new)          # Lattafa Asad
        self.assertIn("I0060501", new)          # JPG Le Male 125ml
        self.assertIn("I0060502", new)          # JPG Le Male 75ml

    def test_sizes_of_one_scent_become_one_product(self):
        new = [r.item for r in partition(
            [i for i in self.items if i.sellable], self.index)["new"]]
        groups = group_items(new)
        le_male = [g for g in groups.values()
                   if any(i.brand == "Jean Paul Gaultier" for i in g)]
        self.assertEqual(len(le_male), 1, "Le Male should be one product, not two")
        self.assertEqual(len(le_male[0]), 2, "with two size variants")

    def test_builds_a_valid_product_payload(self):
        new = [r.item for r in partition(
            [i for i in self.items if i.sellable], self.index)["new"]]
        quotes = {i.supplier_sku: self.engine.quote("ace", i.cost, None, i.msrp) for i in new}
        groups = group_items(new)
        group = next(g for g in groups.values() if g[0].brand == "Jean Paul Gaultier")

        payload = build_product_input(group, quotes, location_id=LOCATION, taken_skus=set())

        self.assertEqual(payload["title"], "Jean Paul Gaultier Le Male EDT for Man")
        self.assertEqual(payload["vendor"], "Jean Paul Gaultier")
        self.assertEqual(payload["status"], "DRAFT")
        self.assertEqual(payload["productOptions"][0]["name"], "Size")
        self.assertEqual([v["optionValues"][0]["name"] for v in payload["variants"]],
                         ["75ml", "125ml"])
        self.assertIn("supplier:ace", payload["tags"])

        variant = payload["variants"][0]
        self.assertEqual(variant["inventoryQuantities"][0]["locationId"], LOCATION)
        self.assertEqual(variant["inventoryQuantities"][0]["quantity"], 11)
        self.assertTrue(variant["inventoryItem"]["tracked"])
        self.assertEqual(variant["inventoryItem"]["cost"], "48.00")
        self.assertEqual(variant["inventoryItem"]["measurement"]["weight"]["value"], 360.0)
        self.assertEqual(variant["barcode"], "8435415011518")
        self.assertTrue(variant["sku"].startswith("BW"))
        self.assertEqual(len(payload["files"]), 2)

    def test_skus_do_not_collide(self):
        new = [r.item for r in partition(
            [i for i in self.items if i.sellable], self.index)["new"]]
        quotes = {i.supplier_sku: self.engine.quote("ace", i.cost) for i in new}
        taken = set()
        for group in group_items(new).values():
            build_product_input(group, quotes, LOCATION, taken_skus=taken)
        self.assertEqual(len(taken), len(new))

    def test_price_is_keyed_just_under_the_competitor(self):
        asad = next(i for i in self.items if i.supplier_sku == "I0091001")
        market = MarketIndex([MarketEntry("Lattafa", "Lattafa Asad EDP", 100, Decimal("74.99"))])
        found = market.lookup(asad.brand, asad.title, asad.size_ml)
        self.assertEqual(found, Decimal("74.99"))

        quote = self.engine.quote("ace", asad.cost, found, asad.msrp)
        self.assertEqual(quote.basis, "market")
        self.assertLess(quote.price, Decimal("74.99"), "we must undercut them")
        self.assertGreaterEqual(quote.price, quote.floor_price)

        # The competitor sells well above our floor, so keying to them earns
        # more than pricing off the floor would. That is the point of keying:
        # be cheapest where it counts, not cheaper than we need to be.
        floor_only = self.engine.quote("ace", asad.cost, None, asad.msrp)
        self.assertGreater(quote.price, floor_only.price)
        self.assertGreater(quote.unit_profit, floor_only.unit_profit)

    def test_a_cheap_competitor_drags_our_price_down_to_the_floor(self):
        asad = next(i for i in self.items if i.supplier_sku == "I0091001")
        floor_only = self.engine.quote("ace", asad.cost, None, asad.msrp)
        # Competitor at $66 is above our floor, so we go under them, not to the floor.
        keen = self.engine.quote("ace", asad.cost, Decimal("66.00"), asad.msrp)
        self.assertLess(keen.price, Decimal("66.00"))
        self.assertGreaterEqual(keen.price, keen.floor_price)
        self.assertLess(keen.price, Decimal("70.99"))
        self.assertGreaterEqual(keen.price, floor_only.floor_price)

    def test_an_item_we_cannot_win_is_held_back(self):
        # Competitor selling at $30 a bottle that costs us $22.50 plus $15 fee.
        asad = next(i for i in self.items if i.supplier_sku == "I0091001")
        quote = self.engine.quote("ace", asad.cost, Decimal("30.00"), asad.msrp)
        self.assertFalse(quote.sellable)
        self.assertIn("below_floor", quote.flags)

    def test_missing_images_are_reported(self):
        gaps = missing_images(self.items)
        self.assertIn("I0091002", {i.supplier_sku for i in gaps})


if __name__ == "__main__":
    unittest.main(verbosity=2)
