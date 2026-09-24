"""The pricing engine.

The problem this solves: Ace charges a flat $15 per order. Spread evenly that
fee is invisible on a $200 bottle and fatal on a $25 one, so a single blanket
markup either prices us out of the designer market or quietly loses money on
the cheap end.

So the price of an item is built in three steps:

  1. Allocate a share of the $15 to the item, weighted so cheap items carry it
     and expensive ones do not.
  2. Compute the floor -- the lowest price that still clears the target margin
     after cost, that allocated fee, and card processing.
  3. Aim at the market: undercut the competitor's price where we know it, but
     never dip below the floor.

Everything is Decimal end to end; money never touches a float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Optional

import yaml

CENT = Decimal("0.01")
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "pricing.yaml"


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class PriceQuote:
    """A price, and the whole argument for why it is that number."""

    price: Decimal
    floor_price: Decimal
    allocated_fee: Decimal
    cost: Decimal
    min_margin: Decimal
    basis: str                       # market | floor | msrp_cap
    compare_at: Optional[Decimal] = None
    market_price: Optional[Decimal] = None
    msrp: Optional[Decimal] = None
    flags: list[str] = field(default_factory=list)

    @property
    def unit_profit(self) -> Decimal:
        """Profit on this unit once the fee share and card fees are paid."""
        return money(self.price - self.cost - self.allocated_fee - self._processing())

    def _processing(self) -> Decimal:
        return money(self.price * PricingEngine.active_rate + PricingEngine.active_fixed)

    @property
    def margin_pct(self) -> Decimal:
        if not self.price:
            return Decimal("0")
        return (self.unit_profit / self.price).quantize(Decimal("0.0001"))

    # Flags that mean "do not list this", as opposed to "worth knowing".
    BLOCKING_FLAGS = ("below_floor", "above_retail", "uncompetitive")

    @property
    def sellable(self) -> bool:
        return self.price > 0 and not any(f in self.flags for f in self.BLOCKING_FLAGS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": str(self.price),
            "compare_at": str(self.compare_at) if self.compare_at else "",
            "cost": str(self.cost),
            "floor_price": str(self.floor_price),
            "allocated_fee": str(self.allocated_fee),
            "unit_profit": str(self.unit_profit),
            "margin_pct": str(self.margin_pct),
            "basis": self.basis,
            "market_price": str(self.market_price) if self.market_price else "",
            "flags": ",".join(self.flags),
        }


class PricingEngine:
    # Set from config on construction so PriceQuote can reach them.
    active_rate = Decimal("0.029")
    active_fixed = Decimal("0.30")

    def __init__(self, config: Optional[dict] = None, config_path: Path = DEFAULT_CONFIG):
        self.config = config if config is not None else yaml.safe_load(Path(config_path).read_text())
        payment = self.config.get("payment", {})
        self.pay_rate = Decimal(str(payment.get("rate", 0)))
        self.pay_fixed = Decimal(str(payment.get("fixed", 0)))
        PricingEngine.active_rate = self.pay_rate
        PricingEngine.active_fixed = self.pay_fixed
        self._assert_bands_monotone()

    def _assert_bands_monotone(self) -> None:
        """Margin bands must step DOWN as price rises.

        Cheap items need the fatter margin because that is where the flat fee
        bites. If a band asked for more margin than the one below it, the floor
        search would have two answers and could return the wrong one.
        """
        margins = [Decimal(str(b["min_margin"])) for b in self.config["margin_floor"]["bands"]]
        ceilings = [b.get("max_price") for b in self.config["margin_floor"]["bands"]]
        if any(c is None for c in ceilings[:-1]) or ceilings[-1] is not None:
            raise ValueError("margin_floor.bands must be ordered by max_price, the last one open-ended")
        if any(later > earlier for earlier, later in zip(margins, margins[1:])):
            raise ValueError(f"margin_floor.bands must not increase with price: {margins}")

    # ------------------------------------------------------------ fee share

    def supplier_rules(self, supplier: str) -> dict:
        return self.config.get("suppliers", {}).get(supplier, {"order_fee": 0})

    def recoverable_fee(self, supplier: str) -> Decimal:
        """The order fee an item has to carry, before the price-band taper.

        The fee is charged per ORDER, so making every item carry all of it
        assumes every customer buys exactly one thing. Two settings say
        otherwise, and both are measured rather than hoped for -- check them
        against real orders with `python -m bw.cli fee-policy`:

          expected_units      how many items an order really holds. Set this
                              BELOW the true average: the average is not a
                              floor, and single-item orders are common.
          shipping_recovers   shipping revenue on small orders, which pays for
                              the handling directly instead of the catalogue
                              paying for it.
        """
        rules = self.supplier_rules(supplier)
        fee = Decimal(str(rules.get("order_fee", 0) or 0))
        if fee <= 0:
            return Decimal("0.00")
        fee = max(Decimal("0"), fee - Decimal(str(rules.get("shipping_recovers", 0) or 0)))
        units = Decimal(str(rules.get("expected_units", 1) or 1))
        return money(fee / max(units, Decimal("1")))

    def allocate_fee(self, supplier: str, base_price: Decimal) -> Decimal:
        """How much of the order fee this item has to carry by itself.

        Full weight below `fee_full_below`, nothing above `fee_free_above`,
        straight line in between.
        """
        rules = self.supplier_rules(supplier)
        fee = self.recoverable_fee(supplier)
        if fee <= 0:
            return Decimal("0.00")

        full_below = Decimal(str(rules.get("fee_full_below", 0) or 0))
        free_above = Decimal(str(rules.get("fee_free_above", 0) or 0))
        if free_above <= full_below:          # no taper configured: everyone pays
            return money(fee)
        if base_price <= full_below:
            return money(fee)
        if base_price >= free_above:
            return Decimal("0.00")
        span = free_above - full_below
        remaining = (free_above - base_price) / span
        return money(fee * remaining)

    # --------------------------------------------------------- margin floor

    def band_margin(self, price: Decimal) -> Decimal:
        for band in self.config["margin_floor"]["bands"]:
            ceiling = band.get("max_price")
            if ceiling is None or price <= Decimal(str(ceiling)):
                return Decimal(str(band["min_margin"]))
        return Decimal(str(self.config["margin_floor"]["bands"][-1]["min_margin"]))

    def _floor_for(self, cost: Decimal, fee: Decimal, margin: Decimal) -> Decimal:
        """Cheapest price where margin still clears after fee and processing.

            (P - cost - fee - rate*P - fixed) / P >= margin
        """
        denominator = Decimal("1") - self.pay_rate - margin
        if denominator <= 0:
            raise ValueError(f"margin {margin} is impossible with a {self.pay_rate} processing rate")
        return money((cost + fee + self.pay_fixed) / denominator)

    def achieved_margin(self, supplier: str, price: Decimal, cost: Decimal) -> Decimal:
        """Margin actually earned at a given price, after fee share and processing."""
        if price <= 0:
            return Decimal("-1")
        fee = self.allocate_fee(supplier, price)
        processing = money(price * self.pay_rate + self.pay_fixed)
        return (price - cost - fee - processing) / price

    def floor_price(self, supplier: str, cost: Decimal) -> tuple[Decimal, Decimal, Decimal]:
        """Cheapest price that clears its own margin band. Returns (floor, fee, margin).

        Both inputs move with the price -- the fee share tapers off as the price
        rises, and the required margin steps down between bands -- so this is
        solved by search rather than formula. It works because the required
        margin never rises with price while the achieved margin always does,
        which makes "clears its band" monotone; `_assert_bands_monotone` holds
        the config to that.

        Solving it directly would land on the wrong side of a band edge: a $5.74
        cost prices at either $35.60 (needing 45%, earning 39% -- invalid) or
        $40.38 (needing 38%, earning 45%). Only the upper one is real.
        """
        cost = money(cost)

        def clears(price: Decimal) -> bool:
            return self.achieved_margin(supplier, price, cost) >= self.band_margin(price)

        low = money(cost)
        high = max(money(cost * 3), Decimal("50.00"))
        for _ in range(40):
            if clears(high):
                break
            high *= 2
        else:
            raise ValueError(f"no price clears the margin floor for cost {cost}")

        while high - low > CENT:
            middle = money((low + high) / 2)
            if middle <= low:
                break
            if clears(middle):
                high = middle
            else:
                low = middle

        return high, self.allocate_fee(supplier, high), self.band_margin(high)

    # -------------------------------------------------------------- rounding

    def _round_charm(self, price: Decimal, floor: Decimal,
                     ceiling: Optional[Decimal] = None) -> Decimal:
        rules = self.config.get("rounding", {})
        endings = [Decimal(str(e)) for e in rules.get("endings", [])]
        if not endings:
            return price
        max_markdown = Decimal(str(rules.get("max_markdown", 0)))

        whole = price.to_integral_value(rounding=ROUND_DOWN)
        candidates = sorted(
            {whole + delta + ending
             for ending in endings
             for delta in (Decimal(-1), Decimal(0), Decimal(1))}
        )

        below = [c for c in candidates
                 if floor <= c <= price and price - c <= max_markdown]
        if below:
            return money(max(below))

        above = [c for c in candidates if c >= max(price, floor)]
        if above:
            rounded = money(min(above))
            # Rounding up must not undo an undercut we were relying on.
            if ceiling is not None and rounded >= ceiling:
                under = [c for c in candidates if floor <= c < ceiling]
                return money(max(under)) if under else price
            return rounded
        return price

    # ----------------------------------------------------------- the quote

    def quote(
        self,
        supplier: str,
        cost: Any,
        market_price: Any = None,
        msrp: Any = None,
    ) -> PriceQuote:
        cost = money(cost)
        floor, fee, margin = self.floor_price(supplier, cost)

        market_cfg = self.config.get("market", {})
        price, basis = floor, "floor"
        flags: list[str] = []

        market = money(market_price) if market_price else None
        msrp_value = money(msrp) if msrp else None

        if market and market > 0:
            undercut = Decimal(str(market_cfg.get("undercut", 0)))
            min_undercut = Decimal(str(market_cfg.get("min_undercut", 0)))
            target = money(min(market * (Decimal("1") - undercut), market - min_undercut))

            if target >= floor:
                price, basis = target, "market"
            elif floor < market:
                # We cannot undercut by the full margin we would like, but we
                # can still come in under them and make money. Worth selling.
                price, basis = floor, "market_tight"
                flags.append("thin_undercut")
                flags.append(f"market_{market}")
            else:
                # They sell it for less than it costs us to sell it. Not a
                # fight worth having -- this one is held back.
                price, basis = floor, "floor"
                flags.append("below_floor")
                flags.append(f"market_{market}")

        if msrp_value:
            cap = money(msrp_value * Decimal(str(market_cfg.get("msrp_cap", 1))))
            if price > cap and cap >= floor:
                price, basis = cap, "msrp_cap"
            # Being under retail is not the same as being worth buying. A
            # discount store priced at 93% of retail is profitable and unsold:
            # the shopper came here precisely because it should be cheaper.
            # Anything we cannot get under this share is held back rather than
            # published to sit there looking expensive.
            # The retail reference is a STAND-IN for the market, used when we
            # have no real competitor price. Where we do have one, and we are
            # under it, that is the actual competitiveness test and the
            # stand-in must not overrule it -- otherwise a clear win gets held
            # back for looking expensive against a number nobody is charging.
            ceiling_share = market_cfg.get("max_share_of_retail")
            beating_market = market is not None and market > 0 and price < market
            if ceiling_share and not beating_market and price > msrp_value * Decimal(str(ceiling_share)):
                flags.append("uncompetitive")
                flags.append(f"retail_{msrp_value}")

        # Never let rounding carry us up to or past the competitor.
        ceiling = market if market and market > floor else None
        price = self._round_charm(price, floor, ceiling)

        if msrp_value and price > msrp_value and market_cfg.get("hold_above_msrp", True):
            # Priced above what this thing sells for in the market. Listing it
            # wins nothing and advertises us as the expensive option, so it is
            # held back instead.
            #
            # Checked on the final price rather than on the floor, because two
            # routes get past a floor test. The cap above is skipped whenever
            # it lands below the floor, and the competitiveness exemption lets
            # a market price through on the grounds that we are undercutting
            # someone -- but undercutting a competitor who is themselves above
            # retail still leaves us above retail. Carolina Herrera Bad Boy
            # went out at $236.99 against a $209 retail reference that way:
            # floor $205.94, cap $198.55 skipped for being under the floor,
            # and a $249.95 competitor waving the rest through.
            flags.append("above_retail")
            flags.append(f"retail_{msrp_value}")

        # Rounding changes the price, which can change the fee share. Re-check
        # so the reported margin is the real one.
        fee = self.allocate_fee(supplier, price)
        margin = self.band_margin(price)

        compare_at = None
        if msrp_value and msrp_value > price:
            compare_at = msrp_value

        return PriceQuote(
            price=price,
            floor_price=floor,
            allocated_fee=fee,
            cost=cost,
            min_margin=margin,
            basis=basis,
            compare_at=compare_at,
            market_price=market,
            msrp=msrp_value,
            flags=flags,
        )

    # ------------------------------------------------------ order economics

    def shipping_charged(self, subtotal: Decimal) -> Decimal:
        """What the customer pays for shipping on a basket this size."""
        order = self.config.get("order", {}) or {}
        threshold = Decimal(str(order.get("free_shipping_threshold", 0) or 0))
        fee = Decimal(str(order.get("shipping_fee", 0) or 0))
        if threshold <= 0 or fee <= 0 or subtotal >= threshold:
            return Decimal("0.00")
        return money(fee)

    def order_economics(self, supplier: str, quotes: list[PriceQuote]) -> dict[str, Any]:
        """True profit on a basket, charging the flat fee once for the order.

        Per-item allocation is a modelling device; this is what the order
        actually earns -- including the shipping the customer pays on a small
        order. That shipping is not a detail: once items are priced on the
        assumption it exists (`shipping_recovers`), a single cheap item loses
        money without it. Leaving it out of this sum would hide exactly the
        risk the sum is for.
        """
        rules = self.supplier_rules(supplier)
        fee = Decimal(str(rules.get("order_fee", 0) or 0))
        revenue = sum((q.price for q in quotes), Decimal("0"))
        cost = sum((q.cost for q in quotes), Decimal("0"))
        shipping = self.shipping_charged(revenue)
        taken = revenue + shipping
        processing = money(taken * self.pay_rate + self.pay_fixed) if taken else Decimal("0")
        profit = money(taken - cost - fee - processing)
        return {
            "items": len(quotes),
            "revenue": money(revenue),
            "shipping": shipping,
            "cost": money(cost),
            "order_fee": money(fee),
            "processing": processing,
            "profit": profit,
            "margin_pct": (profit / taken).quantize(Decimal("0.0001")) if taken else Decimal("0"),
        }

    def break_even_order_value(self, supplier: str, margin: Decimal = Decimal("0")) -> Decimal:
        """Smallest order worth taking at a given blended cost ratio.

        Answers "how big does a cart have to be before the $15 stops hurting?",
        which is the number to set a free-shipping or minimum-order rule from.
        """
        rules = self.supplier_rules(supplier)
        fee = Decimal(str(rules.get("order_fee", 0) or 0))
        if fee <= 0:
            return Decimal("0.00")
        denominator = Decimal("1") - self.pay_rate - margin
        return money((fee + self.pay_fixed) / denominator) if denominator > 0 else Decimal("0")
