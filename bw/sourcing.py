"""Choosing which supplier to buy each item from.

Ace quotes us on more than one list, and the same bottle can appear on several
of them at different costs and different terms. Cheapest cost does not settle
it, because the terms differ: a dropship list charges $15 an order, a wholesale
list does not. A $22 dropship item and a $26 wholesale item are not what they
look like once that fee lands.

So sources are compared on the only number that matters commercially -- the
lowest price we could sell the item for and still clear our margin. That folds
in the fee, the margin band and card costs, so it compares like with like.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from .models import SupplierItem
from .normalize import clean_product_name, normalize_barcode, variant_key
from .pricing import PricingEngine


def identity(item: SupplierItem) -> str:
    """A key the same bottle shares across suppliers.

    Barcode when there is a trustworthy one, since that is global. Otherwise
    the attributes: brand, product, size, concentration, gender, packaging.
    """
    barcode = normalize_barcode(item.barcode)
    if barcode:
        return f"ean:{barcode}"
    name = clean_product_name(item.title, item.brand)
    return "key:" + variant_key(
        item.brand, name, item.size_ml, item.concentration, item.gender, item.tester
    )


@dataclass
class Sourced:
    """One item, and where we decided to buy it."""

    item: SupplierItem
    floor_price: Decimal
    alternatives: list[tuple[SupplierItem, Decimal]] = field(default_factory=list)

    @property
    def tied(self) -> bool:
        """Another list would sell for exactly the same -- no real winner.

        Common between a dropship and a stocked version of one list: above the
        fee taper the order fee is zero either way, so the terms stop mattering.
        """
        return bool(self.alternatives) and self.saving == 0

    @property
    def supplier(self) -> str:
        return self.item.supplier

    @property
    def saving(self) -> Decimal:
        """How much cheaper we can sell it for by using this source instead of
        the next best one."""
        if not self.alternatives:
            return Decimal("0.00")
        return min(price for _, price in self.alternatives) - self.floor_price


def choose_sources(
    feeds: dict[str, Iterable[SupplierItem]],
    engine: Optional[PricingEngine] = None,
    in_stock_only: bool = True,
) -> dict[str, Sourced]:
    """Pick the best supplier for every item across several feeds."""
    engine = engine or PricingEngine()
    candidates: dict[str, list[tuple[SupplierItem, Decimal]]] = {}

    for supplier, items in feeds.items():
        for item in items:
            if not item.sellable:
                continue
            if in_stock_only and not item.in_stock:
                continue
            floor, _, _ = engine.floor_price(supplier, item.cost)
            candidates.setdefault(identity(item), []).append((item, floor))

    chosen: dict[str, Sourced] = {}
    for key, options in candidates.items():
        # Cheapest sellable price wins; more stock breaks a tie.
        options.sort(key=lambda pair: (pair[1], -pair[0].qty))
        best_item, best_floor = options[0]
        chosen[key] = Sourced(best_item, best_floor, options[1:])
    return chosen


def compare(feeds: dict[str, Iterable[SupplierItem]],
            engine: Optional[PricingEngine] = None) -> dict:
    """Summary of how several lists stack up against each other."""
    engine = engine or PricingEngine()
    materialised = {name: list(items) for name, items in feeds.items()}
    chosen = choose_sources(materialised, engine)

    wins: dict[str, int] = {name: 0 for name in materialised}
    overlap = ties = 0
    total_saving = Decimal("0")
    for sourced in chosen.values():
        if sourced.alternatives:
            overlap += 1
        if sourced.tied:
            ties += 1                       # no list is better; do not credit one
            continue
        wins[sourced.supplier] = wins.get(sourced.supplier, 0) + 1
        total_saving += sourced.saving

    return {
        "feed_sizes": {name: len(items) for name, items in materialised.items()},
        "distinct_items": len(chosen),
        "on_more_than_one_list": overlap,
        "ties": ties,
        "wins": wins,
        "total_saving": total_saving.quantize(Decimal("0.01")),
        "chosen": chosen,
    }
