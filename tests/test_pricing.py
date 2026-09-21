import unittest
from decimal import Decimal

from bw.pricing import PricingEngine, money
from support import fixed_engine


class FeeAllocationTests(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()

    def test_cheap_item_carries_the_full_share(self):
        # A $30 bottle is exactly where the flat $15 does the damage. With
        # expected_units at 2 the share is $7.50, and the cheap end carries all
        # of that rather than a tapered part of it.
        self.assertEqual(self.engine.allocate_fee("ace", Decimal("30")),
                         self.engine.recoverable_fee("ace"))
        self.assertEqual(self.engine.recoverable_fee("ace"), Decimal("7.50"))

    def test_assuming_single_item_orders_charges_the_whole_fee(self):
        engine = PricingEngine(config={
            "payment": {"rate": 0.029, "fixed": 0.30},
            "suppliers": {"x": {"order_fee": 15, "expected_units": 1,
                                "fee_full_below": 60, "fee_free_above": 150}},
            "margin_floor": {"bands": [{"max_price": None, "min_margin": 0.3}]},
            "market": {}, "rounding": {"endings": [0.99], "max_markdown": 6},
        })
        self.assertEqual(engine.allocate_fee("x", Decimal("30")), Decimal("15.00"))

    def test_shipping_revenue_reduces_what_the_catalogue_carries(self):
        engine = PricingEngine(config={
            "payment": {"rate": 0.029, "fixed": 0.30},
            "suppliers": {"x": {"order_fee": 15, "expected_units": 2,
                                "shipping_recovers": 12.99,
                                "fee_full_below": 60, "fee_free_above": 150}},
            "margin_floor": {"bands": [{"max_price": None, "min_margin": 0.3}]},
            "market": {}, "rounding": {"endings": [0.99], "max_markdown": 6},
        })
        # (15 - 12.99) / 2
        self.assertEqual(engine.recoverable_fee("x"), Decimal("1.01"))

    def test_shipping_cannot_make_the_fee_negative(self):
        engine = PricingEngine(config={
            "payment": {"rate": 0.029, "fixed": 0.30},
            "suppliers": {"x": {"order_fee": 15, "shipping_recovers": 30}},
            "margin_floor": {"bands": [{"max_price": None, "min_margin": 0.3}]},
            "market": {}, "rounding": {"endings": [0.99], "max_markdown": 6},
        })
        self.assertEqual(engine.recoverable_fee("x"), Decimal("0.00"))

    def test_expensive_item_carries_none(self):
        self.assertEqual(self.engine.allocate_fee("ace", Decimal("200")), Decimal("0.00"))

    def test_fee_tapers_in_between(self):
        # Midpoint of the 60..150 taper: half of whatever the full share is.
        mid = self.engine.allocate_fee("ace", Decimal("105"))
        self.assertEqual(mid, self.engine.recoverable_fee("ace") / 2)
        self.assertGreater(self.engine.allocate_fee("ace", Decimal("70")),
                           self.engine.allocate_fee("ace", Decimal("140")))

    def test_our_own_stock_has_no_dropship_fee(self):
        self.assertEqual(self.engine.allocate_fee("onhand", Decimal("30")), Decimal("0.00"))

    def test_unknown_supplier_defaults_to_no_fee(self):
        self.assertEqual(self.engine.allocate_fee("brand-new-guy", Decimal("30")), Decimal("0.00"))


class FloorTests(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()

    def test_floor_clears_the_stated_margin(self):
        floor, fee, margin = self.engine.floor_price("ace", Decimal("52.70"))
        quote = self.engine.quote("ace", Decimal("52.70"))
        self.assertGreaterEqual(quote.price, floor)
        self.assertGreaterEqual(quote.margin_pct, margin - Decimal("0.005"))

    def test_margin_holds_across_the_whole_cost_range(self):
        # The floor must survive every band edge and every fee taper point.
        for cents in range(300, 30000, 137):
            cost = Decimal(cents) / 100
            quote = self.engine.quote("ace", cost)
            self.assertGreaterEqual(
                quote.margin_pct, quote.min_margin - Decimal("0.005"),
                f"cost {cost} priced at {quote.price} only makes {quote.margin_pct}",
            )
            self.assertGreater(quote.unit_profit, Decimal("0"), f"cost {cost} loses money")

    def test_own_stock_prices_cheaper_than_dropshipped(self):
        ace = self.engine.quote("ace", Decimal("20"))
        ours = self.engine.quote("onhand", Decimal("20"))
        self.assertLess(ours.price, ace.price)


class MarketKeyingTests(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()

    def test_undercuts_the_competitor_when_we_can_afford_to(self):
        quote = self.engine.quote("ace", cost=52.70, market_price=139.99)
        self.assertEqual(quote.basis, "market")
        self.assertLess(quote.price, Decimal("139.99"))
        self.assertGreaterEqual(quote.price, quote.floor_price)

    def test_never_sells_below_the_floor_to_match_a_competitor(self):
        quote = self.engine.quote("ace", cost=52.70, market_price=60.00)
        self.assertEqual(quote.price, self.engine._round_charm(quote.floor_price, quote.floor_price))
        self.assertIn("below_floor", quote.flags)
        self.assertFalse(quote.sellable)

    def test_msrp_caps_the_ask(self):
        quote = self.engine.quote("ace", cost=52.70, market_price=400.00, msrp=120.00)
        self.assertEqual(quote.basis, "msrp_cap")
        self.assertLessEqual(quote.price, Decimal("120.00"))

    def test_msrp_becomes_compare_at(self):
        quote = self.engine.quote("ace", cost=52.70, market_price=110.00, msrp=189.00)
        self.assertEqual(quote.compare_at, Decimal("189.00"))

    def test_msrp_below_price_is_not_used_as_compare_at(self):
        quote = self.engine.quote("ace", cost=52.70, msrp=60.00)
        self.assertIsNone(quote.compare_at)


class RoundingTests(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()

    def test_prices_end_in_charm_endings(self):
        for cost in (9.99, 22.50, 52.70, 84.00, 210.00):
            price = self.engine.quote("ace", cost).price
            self.assertIn(str(price)[-2:], ("99", "95"), f"cost {cost} -> {price}")

    def test_rounding_never_dips_under_the_floor(self):
        for cents in range(500, 20000, 311):
            quote = self.engine.quote("ace", Decimal(cents) / 100)
            self.assertGreaterEqual(quote.price, quote.floor_price)


class OrderEconomicsTests(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()

    def test_flat_fee_is_charged_once_per_order_not_per_item(self):
        quotes = [self.engine.quote("ace", 52.70) for _ in range(3)]
        order = self.engine.order_economics("ace", quotes)
        self.assertEqual(order["order_fee"], Decimal("15.00"))
        # Three items each carrying a fee share, but only one real fee: the
        # basket earns more than the per-item model promises.
        modelled = sum(q.unit_profit for q in quotes)
        self.assertGreater(order["profit"], modelled)

    def test_single_cheap_item_order_still_profits(self):
        quote = self.engine.quote("ace", 12.00)
        order = self.engine.order_economics("ace", [quote])
        self.assertGreater(order["profit"], Decimal("0"),
                           "a one-item order of a cheap bottle must not lose money")

    def test_break_even_order_value(self):
        self.assertGreater(self.engine.break_even_order_value("ace"), Decimal("15"))
        self.assertEqual(self.engine.break_even_order_value("onhand"), Decimal("0.00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class UndercutEdgeTests(unittest.TestCase):
    """The band where we can beat the competitor, but only just.

    Pinned to the strictest fee policy -- the whole $15 on every item -- so
    these keep testing the undercut logic rather than the current fee split.
    """

    def setUp(self):
        self.engine = fixed_engine()

    def test_thin_undercut_is_still_worth_selling(self):
        # Floor is ~$63.10, competitor at $66: the full 5% undercut ($62.70)
        # is unreachable, but we can still be the cheaper listing.
        quote = self.engine.quote("ace", cost=22.50, market_price=66.00)
        self.assertEqual(quote.basis, "market_tight")
        self.assertTrue(quote.sellable, "we can undercut them and profit -- sell it")
        self.assertLess(quote.price, Decimal("66.00"))
        self.assertGreaterEqual(quote.price, quote.floor_price)
        self.assertIn("thin_undercut", quote.flags)

    def test_rounding_never_lifts_us_to_the_competitor_price(self):
        for market in (64.00, 65.50, 66.00, 67.25, 70.00):
            quote = self.engine.quote("ace", cost=22.50, market_price=market)
            if quote.sellable:
                self.assertLess(quote.price, Decimal(str(market)),
                                f"market {market} -> we quoted {quote.price}")

    def test_truly_unbeatable_competitor_is_still_held_back(self):
        quote = self.engine.quote("ace", cost=22.50, market_price=55.00)
        self.assertEqual(quote.basis, "floor")
        self.assertIn("below_floor", quote.flags)
        self.assertFalse(quote.sellable)
