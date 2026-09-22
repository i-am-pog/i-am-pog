"""Recovering a per-order fee without pricing the catalogue out of the market."""

import unittest
from decimal import Decimal

from bw.models import SupplierItem
from bw.policy import FeePolicy, OrderShape, simulate
from support import fixed_engine


def catalogue():
    """A spread of costs, each with a market retail reference."""
    return [
        SupplierItem(supplier="ace", supplier_sku=f"S{index}", title=f"Item {index}",
                     brand="Brand", cost=cost, qty=5, msrp=Decimal(str(cost)) * 3,
                     size_ml=100, size_label="100ml")
        for index, cost in enumerate([6, 12, 22, 35, 48, 64, 95, 140, 210])
    ]


class FeeShareTests(unittest.TestCase):
    def test_assuming_one_item_per_order_charges_the_lot(self):
        self.assertEqual(FeePolicy("solo").per_item_fee(), Decimal("15.00"))

    def test_splitting_over_a_basket(self):
        self.assertEqual(
            FeePolicy("pair", expected_units=Decimal("2")).per_item_fee(), Decimal("7.50"))

    def test_shipping_revenue_carries_most_of_it(self):
        policy = FeePolicy("shipped", expected_units=Decimal("2"),
                           shipping_fee=Decimal("12.99"), free_shipping_over=Decimal("99"))
        self.assertEqual(policy.per_item_fee(), Decimal("1.01"))

    def test_shipping_only_counts_if_we_actually_charge_it(self):
        # A shipping fee with no threshold is never charged, so it recovers nothing.
        policy = FeePolicy("unused", expected_units=Decimal("2"),
                           shipping_fee=Decimal("12.99"))
        self.assertEqual(policy.per_item_fee(), Decimal("7.50"))

    def test_the_fee_share_never_goes_negative(self):
        policy = FeePolicy("generous", shipping_fee=Decimal("40"),
                           free_shipping_over=Decimal("99"))
        self.assertEqual(policy.per_item_fee(), Decimal("0.00"))


class OrderLevelTests(unittest.TestCase):
    def test_shipping_is_charged_below_the_threshold_only(self):
        policy = FeePolicy("x", shipping_fee=Decimal("12.99"),
                           free_shipping_over=Decimal("99"))
        self.assertEqual(policy.shipping_charged(Decimal("60")), Decimal("12.99"))
        self.assertEqual(policy.shipping_charged(Decimal("120")), Decimal("0.00"))

    def test_a_supplier_waiver_applies_above_its_threshold(self):
        policy = FeePolicy("x", waived_over=Decimal("300"))
        self.assertEqual(policy.order_fee_charged(Decimal("250")), Decimal("15.00"))
        self.assertEqual(policy.order_fee_charged(Decimal("350")), Decimal("0.00"))


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.engine = fixed_engine()
        self.items = catalogue()
        self.shapes = [OrderShape(1, Decimal("40")), OrderShape(1, Decimal("25")),
                       OrderShape(3, Decimal("120")), OrderShape(2, Decimal("90"))]

    def test_the_strict_policy_never_loses_on_an_order(self):
        result = simulate(self.items, self.shapes, FeePolicy("strict"), self.engine)
        self.assertEqual(result.orders, len(self.shapes))
        self.assertEqual(result.losing_orders, 0)

    def test_relaxing_the_share_lists_more_of_the_catalogue(self):
        strict = simulate(self.items, self.shapes, FeePolicy("strict"), self.engine)
        relaxed = simulate(self.items, self.shapes,
                           FeePolicy("relaxed", expected_units=Decimal("3")), self.engine)
        self.assertGreater(relaxed.listable, strict.listable)

    def test_an_over_optimistic_share_shows_up_as_losing_orders(self):
        # Assume 8 items an order when the orders are mostly singles.
        reckless = FeePolicy("reckless", expected_units=Decimal("8"))
        singles = [OrderShape(1, Decimal("20"))] * 6
        result = simulate(self.items, singles, reckless, self.engine)
        self.assertGreater(result.losing_orders, 0)
        self.assertLess(result.worst_order, Decimal("0"))

    def test_shipping_revenue_rescues_those_same_orders(self):
        singles = [OrderShape(1, Decimal("20"))] * 6
        rescued = FeePolicy("rescued", expected_units=Decimal("8"),
                            shipping_fee=Decimal("12.99"),
                            free_shipping_over=Decimal("99"))
        result = simulate(self.items, singles, rescued, self.engine)
        self.assertEqual(result.losing_orders, 0)

    def test_the_simulation_is_reproducible(self):
        policy = FeePolicy("x", expected_units=Decimal("2"))
        first = simulate(self.items, self.shapes, policy, self.engine)
        second = simulate(self.items, self.shapes, policy, self.engine)
        self.assertEqual(first.profit, second.profit)

    def test_an_empty_catalogue_is_survivable(self):
        result = simulate([], self.shapes, FeePolicy("x"), self.engine)
        self.assertEqual(result.orders, 0)
        self.assertEqual(result.listable, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WaiverTests(unittest.TestCase):
    """A supplier waiver only helps if orders actually reach it."""

    def setUp(self):
        self.engine = fixed_engine()
        self.items = catalogue()
        # Shaped like the real order book: average around $67.
        self.shapes = [OrderShape(1, Decimal("40")), OrderShape(2, Decimal("76")),
                       OrderShape(3, Decimal("90")), OrderShape(1, Decimal("28"))]

    def test_a_waiver_nobody_reaches_changes_nothing(self):
        # Ace waives its fee over $1,000. No order in the book is close.
        plain = FeePolicy("plain", expected_units=Decimal("2"))
        waived = FeePolicy("waived", expected_units=Decimal("2"),
                           waived_over=Decimal("1000"))
        self.assertEqual(
            simulate(self.items, self.shapes, plain, self.engine).profit,
            simulate(self.items, self.shapes, waived, self.engine).profit,
        )

    def test_a_reachable_waiver_does_help(self):
        plain = FeePolicy("plain", expected_units=Decimal("2"))
        waived = FeePolicy("waived", expected_units=Decimal("2"),
                           waived_over=Decimal("50"))
        self.assertGreater(
            simulate(self.items, self.shapes, waived, self.engine).profit,
            simulate(self.items, self.shapes, plain, self.engine).profit,
        )
