"""Suppliers that run their own Shopify store -- Ace (acegiftsplus.ca) being ours.

A Shopify storefront publishes its catalog at /products.json: titles, vendors,
variants, sizes, prices, images and whether each variant is in stock. That is
the same data the storefront renders, offered as JSON, so reading it needs no
scraping and no browser pretence.

What it does NOT carry is our dealer cost -- the public price is retail. The
`cost` block below says where cost comes from instead: a flat dealer discount
off retail, a wholesale price list joined on SKU, or a field the store exposes
to logged-in dealers. Pricing refuses to guess: an item with no cost is
reported by `probe` and skipped by `plan`.

The retail price is not wasted, though. It becomes the item's MSRP, which caps
what we ask -- so we are never more expensive than the supplier's own shop.
"""

from __future__ import annotations

import csv
import html
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import requests

from ..models import SupplierItem
from ..normalize import (
    is_gift_set,
    is_tester,
    parse_concentration,
    parse_gender,
    parse_size,
)
from .base import clean_money, expand_env

USER_AGENT = "BrandsWarehouse-Sync/1.0 (+https://brandswarehouse.com)"
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str, limit: int = 600) -> str:
    return html.unescape(TAG_RE.sub(" ", text or "")).strip()[:limit]


class ShopifyStoreAdapter:
    def __init__(self, name: str, config: dict[str, Any],
                 session: Optional[requests.Session] = None):
        self.name = name
        self.config = expand_env(config)
        self.domain = str(self.config.get("domain", "")).replace("https://", "").strip("/")
        self.collection = self.config.get("collection") or ""
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._price_list: Optional[dict[str, Decimal]] = None

    # ------------------------------------------------------------------ cost

    def _load_price_list(self) -> dict[str, Decimal]:
        """Dealer costs keyed by SKU, from the wholesale list they send us."""
        if self._price_list is not None:
            return self._price_list

        settings = self.config.get("cost", {}) or {}
        path = settings.get("price_list_path")
        self._price_list = {}
        if not path or not Path(path).exists():
            return self._price_list

        sku_column = settings.get("price_list_sku_column", "sku")
        cost_column = settings.get("price_list_cost_column", "cost")
        with Path(path).open(encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                cleaned = {(k or "").strip().lower(): v for k, v in row.items()}
                sku = str(cleaned.get(sku_column.lower(), "")).strip().upper()
                cost = clean_money(cleaned.get(cost_column.lower()))
                if sku and cost:
                    self._price_list[sku] = Decimal(cost)
        return self._price_list

    def derive_cost(self, sku: str, retail: Optional[Decimal],
                    variant: dict) -> Optional[Decimal]:
        settings = self.config.get("cost", {}) or {}
        mode = (settings.get("mode") or "dealer_discount").lower()

        if mode == "price_list":
            return self._load_price_list().get((sku or "").strip().upper())

        if mode == "field":
            raw = clean_money(variant.get(settings.get("field", "cost")))
            return Decimal(raw) if raw else None

        if mode == "dealer_discount":
            discount = settings.get("dealer_discount")
            if discount is None or retail is None:
                return None
            return (retail * (Decimal("1") - Decimal(str(discount)))).quantize(Decimal("0.01"))

        raise ValueError(f"{self.name}: unknown cost mode {mode!r}")

    # -------------------------------------------------------------- fetching

    def _path(self) -> str:
        if self.collection:
            return f"/collections/{self.collection}/products.json"
        return "/products.json"

    def fetch_products(self) -> list[dict]:
        if not self.domain:
            raise ValueError(f"supplier '{self.name}' needs a `domain`")

        delay = float(self.config.get("delay_seconds", 0.5))
        max_pages = int(self.config.get("max_pages", 200))
        limit = int(self.config.get("page_size", 250))

        products: list[dict] = []
        for page in range(1, max_pages + 1):
            response = self.session.get(
                f"https://{self.domain}{self._path()}",
                params={"limit": limit, "page": page},
                timeout=60,
            )
            response.raise_for_status()
            batch = response.json().get("products", [])
            if not batch:
                break
            products.extend(batch)
            if len(batch) < limit:
                break
            time.sleep(delay)
        return products

    # --------------------------------------------------------------- mapping

    def build_items(self, products: list[dict]) -> list[SupplierItem]:
        qty_settings = self.config.get("qty", {}) or {}
        assumed = int(qty_settings.get("available_qty", 5))
        quantity_field = qty_settings.get("field")

        items: list[SupplierItem] = []
        for product in products:
            brand = (product.get("vendor") or "").strip()
            product_title = (product.get("title") or "").strip()
            images = [i.get("src") for i in product.get("images", []) if i.get("src")]
            description = strip_html(product.get("body_html", ""))
            tags = product.get("tags") or []
            tag_text = " ".join(tags) if isinstance(tags, list) else str(tags)
            product_type = product.get("product_type") or ""

            for variant in product.get("variants", []):
                variant_title = (variant.get("title") or "").strip()
                # Shopify's placeholder for a product with no real options.
                if variant_title.lower() == "default title":
                    variant_title = ""

                retail_raw = clean_money(variant.get("price"))
                retail = Decimal(retail_raw) if retail_raw else None

                sku = (variant.get("sku") or "").strip()
                if not sku:
                    # No SKU published: fall back to Shopify's own ids, which are
                    # stable for as long as the product exists.
                    sku = f"{product.get('id')}-{variant.get('id')}"

                size_ml, size_label = parse_size(variant_title)
                if size_ml is None:
                    size_ml, size_label = parse_size(product_title)

                if quantity_field and variant.get(quantity_field) is not None:
                    qty = int(variant.get(quantity_field) or 0)
                else:
                    qty = assumed if variant.get("available") else 0

                context = f"{product_title} {variant_title} {tag_text} {product_type}"
                variant_image = (variant.get("featured_image") or {}).get("src") \
                    if isinstance(variant.get("featured_image"), dict) else None

                items.append(SupplierItem(
                    supplier=self.name,
                    supplier_sku=sku,
                    title=f"{product_title} {variant_title}".strip(),
                    brand=brand,
                    cost=self.derive_cost(sku, retail, variant),
                    qty=qty,
                    barcode=variant.get("barcode"),     # usually absent here
                    # Their shelf price is our ceiling: we never ask more than
                    # the supplier's own store does.
                    msrp=retail,
                    size_ml=size_ml,
                    size_label=size_label,
                    concentration=parse_concentration(context),
                    gender=parse_gender(context),
                    tester=is_tester(context),
                    gift_set=is_gift_set(context) or "gift" in product_type.lower(),
                    image_urls=([variant_image] if variant_image else []) + images,
                    description=description,
                    raw={"product_id": product.get("id"), "variant_id": variant.get("id"),
                         "handle": product.get("handle"), "retail": str(retail or ""),
                         "product_type": product_type},
                ))
        return items

    def fetch(self) -> list[SupplierItem]:
        return self.build_items(self.fetch_products())
