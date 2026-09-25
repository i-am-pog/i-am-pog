"""Turning supplier rows into Shopify listings.

Sizes of the same scent become variants of one product rather than separate
listings -- that is how the store's own import sheets were built, and it is
what stops "212 50ml" and "212 100ml" competing with each other in search.

Everything is created as a DRAFT. Nothing this pipeline builds goes on sale
without someone looking at it first.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from .models import SupplierItem
from .normalize import (
    brand_key,
    clean_product_name,
    estimate_grams,
    handle as make_handle,
    listing_title,
    title_case,
    normalize_barcode,
    product_key,
)
from .pricing import PriceQuote

PRODUCT_TYPE_BY_CONCENTRATION = {
    "EDT": "Fragrance", "EDP": "Fragrance", "EDC": "Fragrance",
    "Parfum": "Fragrance", "Extrait": "Fragrance", "Cologne": "Fragrance",
    "Eau Fraiche": "Fragrance", "Body Spray": "Body Spray", "Body Mist": "Body Spray",
}


def group_items(items: Iterable[SupplierItem]) -> dict[str, list[SupplierItem]]:
    """Collect sizes of the same scent under one product key."""
    groups: dict[str, list[SupplierItem]] = defaultdict(list)
    for item in items:
        name = clean_product_name(item.title, item.brand)
        key = product_key(item.brand, name, item.concentration, item.gender, item.tester)
        groups[key].append(item)
    for group in groups.values():
        group.sort(key=lambda i: (i.size_ml or 0))
    return dict(groups)


def build_sku(item: SupplierItem, prefix: str = "BW", taken: Optional[set[str]] = None) -> str:
    """Our own SKU, derived from the supplier's so the two stay traceable."""
    trimmed = "".join(c for c in (item.supplier_sku or item.title) if c.isalnum()).upper()
    sku = f"{prefix}{trimmed[-8:]}" if trimmed else f"{prefix}{abs(hash(item.title)) % 10**8:08d}"
    if taken is None:
        return sku
    candidate, suffix = sku, 1
    while candidate in taken:
        suffix += 1
        candidate = f"{sku}-{suffix}"
    taken.add(candidate)
    return candidate


def build_description(brand: str, name: str, items: list[SupplierItem]) -> str:
    first = items[0]
    # A signature scent carries no name of its own beyond the house's.
    name = title_case(name) if name else brand      # feeds shout: "LE MALE" -> "Le Male"
    sizes = ", ".join(i.size_label for i in items if i.size_label)
    bullets = []
    if first.concentration:
        bullets.append(f"<li>Concentration: {first.concentration}</li>")
    if first.gender:
        bullets.append(f"<li>For: {first.gender}</li>")
    if sizes:
        bullets.append(f"<li>Available sizes: {sizes}</li>")
    if first.tester:
        bullets.append("<li>Tester packaging</li>")

    # "Bob Mackie by Bob Mackie" reads like a mistake, because it is one.
    # Compared on the same key the title uses, or the two disagree over a
    # hyphen and you get "Jean Louis Scherrer by Jean-Louis Scherrer".
    same = brand_key(name) == brand_key(brand)
    lead = f"<p>{brand}.</p>" if same else f"<p>{name} by {brand}.</p>"
    supplied = (first.description or "").strip()
    extra = f"<p><em>{supplied}</em></p>" if supplied else ""
    return (
        f"{lead}"
        f"<ul>{''.join(bullets)}</ul>"
        f"{extra}"
    )


def build_tags(brand: str, items: list[SupplierItem], supplier: str) -> list[str]:
    first = items[0]
    tags = {brand, first.gender or "Unisex"}
    tags.add("Gift Set" if first.gift_set else PRODUCT_TYPE_BY_CONCENTRATION.get(
        first.concentration, "Fragrance"))
    if first.concentration:
        tags.add(first.concentration)
    if first.tester:
        tags.add("Tester")
    tags.add(f"supplier:{supplier}")
    return sorted(t for t in tags if t)


def build_product_input(
    items: list[SupplierItem],
    quotes: dict[str, PriceQuote],
    location_id: str,
    sku_prefix: str = "BW",
    taken_skus: Optional[set[str]] = None,
    publish_status: str = "DRAFT",
) -> dict:
    """One ProductSetInput covering every size we carry of this scent.

    `quotes` is keyed by supplier SKU. Items without a quote are left out --
    that is how an item priced below its floor stays off the site.
    """
    priced = [i for i in items if i.supplier_sku in quotes]
    if not priced:
        raise ValueError("no priced items in group")

    first = priced[0]
    brand = first.brand
    name = clean_product_name(first.title, brand)
    title = listing_title(brand, name, first.gender, first.concentration, first.tester)

    files, variants = [], []
    seen_images: set[str] = set()
    used_labels: set[str] = set()

    for item in priced:
        quote = quotes[item.supplier_sku]
        # Shopify rejects a product with two identical option values, which is
        # what a supplier of non-fragrance goods (no size on anything) would
        # otherwise produce.
        size_label = item.size_label or "Default"
        if size_label in used_labels:
            size_label = f"{size_label} ({item.supplier_sku})"
        used_labels.add(size_label)

        image = item.image_urls[0] if item.image_urls else None
        if image and image not in seen_images:
            seen_images.add(image)
            files.append({
                "originalSource": image,
                "contentType": "IMAGE",
                "alt": f"{title} {size_label}".strip(),
            })

        variant: dict = {
            "optionValues": [{"optionName": "Size", "name": size_label}],
            "price": str(quote.price),
            "sku": build_sku(item, sku_prefix, taken_skus),
            "inventoryPolicy": "DENY",
            "taxable": True,
            "inventoryItem": {
                "cost": str(quote.cost),
                "tracked": True,
                "requiresShipping": True,
                "measurement": {
                    "weight": {"value": float(estimate_grams(item.size_ml)), "unit": "GRAMS"}
                },
            },
            "inventoryQuantities": [{
                "locationId": location_id,
                "name": "available",
                "quantity": max(0, int(item.qty)),
            }],
            "metafields": [
                {"namespace": "supply", "key": "supplier_sku",
                 "value": item.supplier_sku, "type": "single_line_text_field"},
            ],
        }
        if quote.compare_at:
            variant["compareAtPrice"] = str(quote.compare_at)
        barcode = normalize_barcode(item.barcode)
        if barcode:
            variant["barcode"] = barcode
        if image:
            variant["file"] = {"originalSource": image}
        variants.append(variant)

    product: dict = {
        "title": title,
        "handle": make_handle(title),
        "vendor": brand,
        "productType": "Gift Set" if first.gift_set else PRODUCT_TYPE_BY_CONCENTRATION.get(
            first.concentration, "Fragrance"),
        "status": publish_status,
        "descriptionHtml": build_description(brand, name, priced),
        "tags": build_tags(brand, priced, first.supplier),
        "seo": {
            "title": f"{title} | Brands Warehouse"[:70],
            "description": (
                f"Shop {title} at Brands Warehouse. Authentic fragrance"
                + (f", available in {', '.join(i.size_label for i in priced if i.size_label)}."
                   if any(i.size_label for i in priced) else ".")
            )[:320],
        },
        "productOptions": [{
            "name": "Size",
            "position": 1,
            "values": [{"name": v["optionValues"][0]["name"]} for v in variants],
        }],
        "variants": variants,
        "metafields": [
            {"namespace": "supply", "key": "supplier",
             "value": first.supplier, "type": "single_line_text_field"},
        ],
    }
    if files:
        product["files"] = files
    return product


def missing_images(items: Iterable[SupplierItem]) -> list[SupplierItem]:
    """Items we would publish with no photo -- worth chasing before they go live."""
    return [i for i in items if not i.image_urls]
