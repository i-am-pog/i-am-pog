"""How much of the flat order fee each item should carry.

The fee is charged per ORDER, so loading the whole $15 onto every item assumes
every customer buys exactly one thing. They do not -- but enough of them do
that assuming the average is not safe either. On Brands Warehouse's own order
history: 2.89 units per order on average, and 37% of orders are a single item.

Four levers, and they combine:

  expected_units    divide the fee by what an order really holds
  shipping          charge it below a free-shipping threshold, so small orders
                    pay for their own handling
  waiver            if the supplier drops the fee above some order value,
                    orders over that carry nothing
  stocking          items we hold have no per-order fee at all

Nothing here is a guess that has to be believed. `simulate` replays real orders
against the real catalogue and reports what each policy would actually have
earned, and how many orders it would have lost money on.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from .pricing import PricingEngine, money

SHAPES_PATH = Path(__file__).resolve().parent.parent / "data" / "order_shapes.csv"


@dataclass(frozen=True)
class OrderShape:
    """One historical order, reduced to what matters: how many, how much."""

    units: int
    subtotal: Decimal


def load_shapes(path: Path = SHAPES_PATH) -> list[OrderShape]:
    if not path.exists():
        raise FileNotFoundError(
            f"no order history at {path} -- run `python -m bw.cli pull-orders` first"
        )
    shapes = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            units = int(row["units"])
            subtotal = Decimal(row["subtotal"])
            if units > 0 and subtotal > 0:
                shapes.append(OrderShape(units, subtotal))
    return shapes


@dataclass
class FeePolicy:
    """A way of recovering the flat order fee."""

    name: str
    order_fee: Decimal = Decimal("15.00")
    expected_units: Decimal = Decimal("1")      # 1 = assume the worst
    shipping_fee: Decimal = Decimal("0")        # charged below the threshold
    free_shipping_over: Decimal = Decimal("0")
    waived_over: Decimal = Decimal("0")         # supplier drops the fee above this
    fee_full_below: Decimal = Decimal("60")
    fee_free_above: Decimal = Decimal("150")

    def per_item_fee(self) -> Decimal:
        """What one item should carry, before the price-band taper."""
        recovered = self.shipping_fee if self.free_shipping_over > 0 else Decimal("0")
        residual = max(Decimal("0"), self.order_fee - recovered)
        return money(residual / max(self.expected_units, Decimal("1")))

    def supplier_rules(self) -> dict:
        """The shape config/pricing.yaml uses, so the engine can price with it."""
        return {
            "order_fee": str(self.per_item_fee()),
            "fee_full_below": str(self.fee_full_below),
            "fee_free_above": str(self.fee_free_above),
        }

    def order_fee_charged(self, subtotal: Decimal) -> Decimal:
        if self.waived_over and subtotal >= self.waived_over:
            return Decimal("0.00")
        return self.order_fee

    def shipping_charged(self, subtotal: Decimal) -> Decimal:
        if self.free_shipping_over <= 0 or subtotal >= self.free_shipping_over:
            return Decimal("0.00")
        return self.shipping_fee


@dataclass
class SimulationResult:
    policy: str
    orders: int = 0
    losing_orders: int = 0
    revenue: Decimal = Decimal("0")
    profit: Decimal = Decimal("0")
    worst_order: Decimal = Decimal("0")
    listable: int = 0
    catalogue: int = 0
    unpriceable: list = field(default_factory=list)

    @property
    def loss_rate(self) -> Decimal:
        if not self.orders:
            return Decimal("0")
        return (Decimal(self.losing_orders) / self.orders).quantize(Decimal("0.001"))

    @property
    def margin_pct(self) -> Decimal:
        if not self.revenue:
            return Decimal("0")
        return (self.profit / self.revenue).quantize(Decimal("0.0001"))


def price_catalogue(items, policy: FeePolicy, engine: PricingEngine) -> list[tuple[Decimal, Decimal]]:
    """Quote every item under this policy. Returns (price, cost) for the sellable ones."""
    engine.config.setdefault("suppliers", {})["_policy"] = policy.supplier_rules()
    priced = []
    for item in items:
        quote = engine.quote("_policy", item.cost, None, item.msrp)
        if quote.sellable:
            priced.append((quote.price, quote.cost))
    return priced


def simulate(
    items,
    shapes: Iterable[OrderShape],
    policy: FeePolicy,
    engine: Optional[PricingEngine] = None,
    seed: int = 20260921,
) -> SimulationResult:
    """Replay real orders against the real catalogue under one fee policy.

    Each historical order says "N units totalling V". We fill it with the
    catalogue items priced nearest V/N, so the basket reflects both what the
    customer spent and what we would actually be selling at that price.
    """
    engine = engine or PricingEngine()
    items = list(items)
    priced = price_catalogue(items, policy, engine)

    result = SimulationResult(policy=policy.name, catalogue=len(items), listable=len(priced))
    if not priced:
        return result

    priced.sort(key=lambda pair: pair[0])
    prices = [p for p, _ in priced]
    rng = random.Random(seed)

    for shape in shapes:
        target = shape.subtotal / shape.units
        # Items near that price point, chosen from a window so a basket is not
        # N copies of the same bottle.
        centre = min(range(len(prices)), key=lambda i: abs(prices[i] - target))
        low = max(0, centre - 25)
        high = min(len(priced), centre + 25)
        basket = [priced[rng.randrange(low, high)] for _ in range(shape.units)]

        revenue = sum((price for price, _ in basket), Decimal("0"))
        cost = sum((c for _, c in basket), Decimal("0"))
        shipping = policy.shipping_charged(revenue)
        fee = policy.order_fee_charged(revenue)
        processing = money((revenue + shipping) * engine.pay_rate + engine.pay_fixed)
        profit = money(revenue + shipping - cost - fee - processing)

        result.orders += 1
        result.revenue += revenue + shipping
        result.profit += profit
        if profit < 0:
            result.losing_orders += 1
        result.worst_order = min(result.worst_order, profit)

    return result
