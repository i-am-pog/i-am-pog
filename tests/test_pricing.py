import unittest
from decimal import Decimal

from bw.pricing import PricingEngine, money
from support import fixed_engine


class FeeAllocationTests(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine()

    def test_cheap_item_carries_the_full_share(self):
        # The cheap end carries the whole share, not a tapered part of it.
        # What the share IS depends on the live fee policy, so that is not
        # asserted here -- fee-policy is where that decision is tested.
        self.assertEqual(self.engine.allocate_fee("ace", Decimal("30")),
                         self.engine.recoverable_fee("ace"))
        self.assertGreater(self.engine.recoverable_fee("ace"), Decimal("0"))

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
        self.assertEqual(mid, money(self.engine.recoverable_fee("ace") / 2))
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
        self.engine = fixed_engine()

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


class ShippingBackedOrderTests(unittest.TestCase):
    """When prices assume shipping revenue, the shipping has to be real.

    With `shipping_recovers` set, items are priced as though small orders pay
    for their own handling. If that shipping charge is not actually live on the
    site, a single cheap item loses money -- these pin both halves of that.
    """

    CONFIG = {
        "payment": {"rate": 0.029, "fixed": 0.30},
        "suppliers": {"ace": {"order_fee": 15.00, "expected_units": 2,
                              "shipping_recovers": 12.99,
                              "fee_full_below": 60.00, "fee_free_above": 150.00}},
        "margin_floor": {"bands": [
            {"max_price": 40.00, "min_margin": 0.45},
            {"max_price": None, "min_margin": 0.30},
        ]},
        "market": {"undercut": 0.05, "min_undercut": 1.00, "msrp_cap": 0.95},
        "rounding": {"endings": [0.99, 0.95], "max_markdown": 6.00},
        "order": {"free_shipping_threshold": 99.00, "shipping_fee": 12.99},
    }

    def engine(self, **order_overrides):
        import copy
        config = copy.deepcopy(self.CONFIG)
        config["order"].update(order_overrides)
        return PricingEngine(config=config)

    def test_a_single_cheap_item_profits_once_shipping_is_charged(self):
        engine = self.engine()
        order = engine.order_economics("ace", [engine.quote("ace", 12.00)])
        self.assertEqual(order["shipping"], Decimal("12.99"))
        self.assertGreater(order["profit"], Decimal("0"))

    def test_the_same_order_loses_money_with_no_shipping_charge(self):
        # The failure mode to watch: prices set assuming shipping revenue, but
        # the shipping rule never actually configured on the storefront.
        engine = self.engine(shipping_fee=0)
        order = engine.order_economics("ace", [engine.quote("ace", 12.00)])
        self.assertEqual(order["shipping"], Decimal("0.00"))
        self.assertLess(order["profit"], Decimal("0"))

    def test_a_big_order_needs_no_shipping_revenue(self):
        engine = self.engine()
        quotes = [engine.quote("ace", 64.00) for _ in range(3)]
        order = engine.order_economics("ace", quotes)
        self.assertEqual(order["shipping"], Decimal("0.00"), "over the threshold")
        self.assertGreater(order["profit"], Decimal("0"))



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


class CompetitivenessTests(unittest.TestCase):
    """Clearing the floor only means it makes money if it sells."""

    CONFIG = {
        "payment": {"rate": 0.029, "fixed": 0.30},
        "suppliers": {"ace": {"order_fee": 15.00, "expected_units": 2,
                              "shipping_recovers": 12.99,
                              "fee_full_below": 60.00, "fee_free_above": 150.00}},
        "margin_floor": {"bands": [{"max_price": None, "min_margin": 0.30}]},
        "market": {"undercut": 0.05, "min_undercut": 1.00, "msrp_cap": 0.95,
                   "hold_above_msrp": True, "max_share_of_retail": 0.85},
        "rounding": {"endings": [0.99, 0.95], "max_markdown": 6.00},
        "order": {},
    }

    def setUp(self):
        import copy
        self.engine = PricingEngine(config=copy.deepcopy(self.CONFIG))

    def test_a_keenly_priced_item_is_fine(self):
        # $30 cost against $100 retail: lands well under the ceiling.
        quote = self.engine.quote("ace", Decimal("30"), None, Decimal("100"))
        self.assertTrue(quote.sellable)
        self.assertLess(quote.price / Decimal("100"), Decimal("0.85"))

    # $35 cost against $60 retail prices at 92% of retail: under the market,
    # but far too close to it. This case isolates the ceiling -- a thinner one
    # like $22 against $29 also trips `above_retail`, so it would pass whether
    # the ceiling worked or not.
    NEAR_RETAIL = (Decimal("35"), Decimal("60"))

    def test_an_item_too_close_to_retail_is_held_back(self):
        cost, retail = self.NEAR_RETAIL
        quote = self.engine.quote("ace", cost, None, retail)
        self.assertIn("uncompetitive", quote.flags)
        self.assertNotIn("above_retail", quote.flags, "this one is under retail")
        self.assertFalse(quote.sellable)

    def test_uncompetitive_is_distinct_from_unprofitable(self):
        cost, retail = self.NEAR_RETAIL
        quote = self.engine.quote("ace", cost, None, retail)
        self.assertNotIn("below_floor", quote.flags)
        self.assertLess(quote.price, retail)
        self.assertGreater(quote.unit_profit, Decimal("0"),
                           "it does make money -- it just will not sell")

    def test_the_ceiling_can_be_turned_off(self):
        import copy
        cost, retail = self.NEAR_RETAIL
        config = copy.deepcopy(self.CONFIG)
        config["market"].pop("max_share_of_retail")
        engine = PricingEngine(config=config)
        self.assertTrue(engine.quote("ace", cost, None, retail).sellable)

    def test_no_retail_reference_means_no_ceiling(self):
        # Nothing to measure against, so the item is judged on margin alone.
        cost, _ = self.NEAR_RETAIL
        self.assertTrue(self.engine.quote("ace", cost).sellable)


class CeilingVersusRealMarketTests(unittest.TestCase):
    """A real competitor price outranks the retail stand-in."""

    def setUp(self):
        import copy
        self.engine = PricingEngine(config=copy.deepcopy(CompetitivenessTests.CONFIG))

    def test_a_win_against_the_competitor_is_not_held_back(self):
        # Retail says $60 and we land at 92% of it, which alone would be held.
        # But the competitor is actually charging $70, so we are the cheap one.
        quote = self.engine.quote("ace", Decimal("35"), Decimal("70"), Decimal("60"))
        self.assertTrue(quote.sellable, "we undercut them; that is what matters")
        self.assertNotIn("uncompetitive", quote.flags)

    def test_the_ceiling_still_applies_with_no_competitor_price(self):
        quote = self.engine.quote("ace", Decimal("35"), None, Decimal("60"))
        self.assertIn("uncompetitive", quote.flags)

    def test_the_ceiling_still_applies_when_we_cannot_beat_them(self):
        # Competitor at $50, our floor above it: no win, so the stand-in rules.
        quote = self.engine.quote("ace", Decimal("35"), Decimal("50"), Decimal("60"))
        self.assertFalse(quote.sellable)


class MarketMatchStrictnessTests(unittest.TestCase):
    """A near match on a competitor's catalogue is a different product."""

    def index(self):
        from bw.market import MarketIndex
        from bw.market.shopify_store import MarketEntry
        return MarketIndex([
            MarketEntry("Armaf", "Armaf Club De Nuit Intense Overdose", 100, Decimal("69.15")),
            MarketEntry("Armaf", "Armaf Odyssey Soda Pop", 100, Decimal("43.85")),
        ])

    def test_an_extra_word_is_a_different_product(self):
        # "Club De Nuit Intense" is not "Club De Nuit Intense Overdose", and
        # pricing one against the other is a $40 error.
        self.assertIsNone(self.index().lookup("Armaf", "Club De Nuit Intense", 100))

    def test_the_exact_product_still_matches(self):
        self.assertEqual(
            self.index().lookup("Armaf", "Armaf Odyssey Soda Pop /Woman", 100),
            Decimal("43.85"))

    def test_the_wrong_size_is_the_wrong_price(self):
        self.assertIsNone(self.index().lookup("Armaf", "Armaf Odyssey Soda Pop", 50))

    def test_letters_alone_do_not_make_a_match(self):
        # "Odyssey Mandarin" scores respectably against "Ombre D'Or" on
        # characters; it is plainly a different bottle.
        self.assertIsNone(self.index().lookup("Armaf", "Odyssey Mandarin", 100))


class NeverPricedAboveRetail(unittest.TestCase):
    """A competitor above retail must not drag our price above retail with it."""

    def setUp(self):
        self.engine = fixed_engine()

    def test_competitor_above_retail_does_not_licence_an_above_retail_price(self):
        # Carolina Herrera Bad Boy, live: cost $142, retail $209, a competitor
        # at $249.95. The retail cap ($198.55) lands below the floor ($205.94)
        # and is skipped, and undercutting the competitor used to wave the rest
        # through -- it went out at $236.99, above its own retail reference.
        quote = self.engine.quote("ace", Decimal("142"), Decimal("249.95"), Decimal("209"))
        self.assertIn("above_retail", quote.flags)
        self.assertFalse(quote.sellable)

    def test_beating_a_competitor_still_allowed_below_retail(self):
        # The exemption this guard narrows is a real one: cheap cost, a
        # competitor far above us, a price under retail. That still sells.
        quote = self.engine.quote("ace", Decimal("55"), Decimal("166.95"), Decimal("130"))
        self.assertLess(quote.price, Decimal("130"))
        self.assertNotIn("above_retail", quote.flags)
        self.assertTrue(quote.sellable)

    def test_floor_above_retail_is_still_held(self):
        # The case the old floor-based check covered, kept working.
        quote = self.engine.quote("ace", Decimal("120"), None, Decimal("100"))
        self.assertIn("above_retail", quote.flags)
        self.assertFalse(quote.sellable)
