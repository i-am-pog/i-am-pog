"""Canonical data model.

Every supplier feed — Ace's portal, our own on-hand sheet, a partner's
consignment list — gets normalized into SupplierItem. Everything downstream
(matching, pricing, the Shopify writer) only ever sees this shape, so adding
a supplier never means touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Optional


def _dec(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass
class SupplierItem:
    """One purchasable unit from a supplier, in canonical form."""

    supplier: str
    supplier_sku: str
    title: str
    brand: str = ""
    cost: Optional[Decimal] = None          # what we pay, CAD, per unit
    qty: int = 0                            # units the supplier has right now
    barcode: Optional[str] = None
    msrp: Optional[Decimal] = None

    size_ml: Optional[int] = None
    size_label: str = ""                    # "100ml", "3 x 10ml"
    concentration: str = ""                 # EDT / EDP / Parfum / ...
    gender: str = ""                        # Man / Woman / Unisex
    tester: bool = False
    gift_set: bool = False

    image_urls: list[str] = field(default_factory=list)
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cost = _dec(self.cost)
        self.msrp = _dec(self.msrp)

    @property
    def in_stock(self) -> bool:
        return self.qty > 0

    @property
    def sellable(self) -> bool:
        """Enough information to build a real listing from."""
        return bool(self.title and self.cost and self.cost > 0)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        for key in ("cost", "msrp"):
            if d[key] is not None:
                d[key] = str(d[key])
        return d


@dataclass
class CatalogVariant:
    """A variant already live (or drafted) in our Shopify store."""

    variant_id: str
    product_id: str
    sku: str = ""
    barcode: str = ""
    title: str = ""
    product_title: str = ""
    vendor: str = ""
    price: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    inventory_item_id: str = ""
    inventory_qty: int = 0
    status: str = ""
    supplier: str = ""                      # from the supply.supplier metafield
    supplier_sku: str = ""

    def __post_init__(self) -> None:
        self.price = _dec(self.price)
        self.cost = _dec(self.cost)


@dataclass
class MatchResult:
    """How a supplier item lines up against our catalog."""

    item: SupplierItem
    variant: Optional[CatalogVariant] = None
    method: str = "none"                    # barcode | supplier_sku | sku | key | fuzzy | none
    score: float = 0.0

    @property
    def is_new(self) -> bool:
        return self.variant is None

    @property
    def needs_review(self) -> bool:
        return self.method == "fuzzy"
