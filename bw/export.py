"""Turning a plan into a Shopify product-import CSV.

The API route needs an admin token this machine does not have, and Shopify's
own importer is the path that already works for this store. So a plan becomes
a CSV in Shopify's 'Products > Import' shape instead.

Two things about that shape are easy to get wrong and expensive to discover
after the fact:

  - A product with several variants is several ROWS. Only the first carries the
    product-level columns (title, description, vendor, tags, SEO); the rest
    repeat the handle and leave those blank. Filling them on every row makes
    Shopify treat each row as a fresh product and the last one wins.

  - Extra images are rows of their own too: handle plus Image Src plus Image
    Position, nothing else. A product row can only carry its first image.

Status is DRAFT for everything. Nothing reaches the storefront until someone
looks at it.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Shopify accepts a subset and ignores unknown columns, but the importer is
# order-insensitive and name-sensitive, so these spellings matter.
COLUMNS = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value",
    "Variant SKU", "Variant Grams", "Variant Inventory Tracker",
    "Variant Inventory Qty", "Variant Inventory Policy",
    "Variant Fulfillment Service", "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable",
    "Image Src", "Image Position", "Image Alt Text",
    "SEO Title", "SEO Description", "Status", "Cost per item",
]


def _weight_grams(variant: dict) -> str:
    measurement = ((variant.get("inventoryItem") or {}).get("measurement") or {})
    weight = (measurement.get("weight") or {})
    value = weight.get("value")
    if value is None:
        return ""
    # The plan records grams; anything else would silently misprice shipping.
    if (weight.get("unit") or "GRAMS").upper() != "GRAMS":
        raise ValueError(f"unexpected weight unit {weight.get('unit')!r}")
    return str(int(round(float(value))))


def image_urls(product: dict) -> list[str]:
    """Every distinct image on the product, first one first."""
    urls: list[str] = []
    for entry in product.get("files") or []:
        url = entry.get("originalSource")
        if url and url not in urls:
            urls.append(url)
    for variant in product.get("variants") or []:
        url = (variant.get("file") or {}).get("originalSource")
        if url and url not in urls:
            urls.append(url)
    return urls


def product_rows(product: dict) -> list[dict[str, Any]]:
    """One product as the rows Shopify's importer expects."""
    handle = product["handle"]
    options = product.get("productOptions") or [{}]
    option_name = (options[0].get("name") or "Title")
    images = image_urls(product)
    tags = product.get("tags") or []

    rows: list[dict[str, Any]] = []
    for index, variant in enumerate(product.get("variants") or []):
        option_value = ""
        for pair in variant.get("optionValues") or []:
            if pair.get("optionName") == option_name:
                option_value = pair.get("name") or ""
        quantities = variant.get("inventoryQuantities") or [{}]
        row = {c: "" for c in COLUMNS}
        row.update({
            "Handle": handle,
            "Option1 Name": option_name,
            "Option1 Value": option_value,
            "Variant SKU": variant.get("sku", ""),
            "Variant Grams": _weight_grams(variant),
            "Variant Inventory Tracker": "shopify",
            "Variant Inventory Qty": str(quantities[0].get("quantity", 0)),
            "Variant Inventory Policy": (variant.get("inventoryPolicy") or "DENY").lower(),
            "Variant Fulfillment Service": "manual",
            "Variant Price": variant.get("price", ""),
            "Variant Compare At Price": variant.get("compareAtPrice") or "",
            "Variant Requires Shipping": "TRUE",
            "Variant Taxable": "TRUE" if variant.get("taxable", True) else "FALSE",
            "Cost per item": (variant.get("inventoryItem") or {}).get("cost", ""),
            "Status": (product.get("status") or "DRAFT").lower(),
        })
        # Product-level columns belong to the first row only. Repeating them
        # makes the importer treat every row as its own product.
        if index == 0:
            row.update({
                "Title": product.get("title", ""),
                "Body (HTML)": product.get("descriptionHtml", ""),
                "Vendor": product.get("vendor", ""),
                "Type": product.get("productType", ""),
                "Tags": ", ".join(tags),
                "Published": "FALSE",
                "SEO Title": (product.get("seo") or {}).get("title", ""),
                "SEO Description": (product.get("seo") or {}).get("description", ""),
            })
            if images:
                row["Image Src"] = images[0]
                row["Image Position"] = "1"
                row["Image Alt Text"] = product.get("title", "")
        rows.append(row)

    # Second and later images get bare rows of their own.
    for position, url in enumerate(images[1:], start=2):
        extra = {c: "" for c in COLUMNS}
        extra.update({"Handle": handle, "Image Src": url,
                      "Image Position": str(position)})
        rows.append(extra)
    return rows


def plan_rows(products: Iterable[dict], with_images_only: bool = False,
              limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Every product as importer rows, newest-first order preserved.

    Refuses to emit two products under one handle. Shopify merges rows by
    handle, so a collision does not fail the import -- it quietly folds one
    product into another and the last rows win. That is the kind of thing you
    find out weeks later from a customer.
    """
    rows: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    written = 0
    for product in products:
        if with_images_only and not image_urls(product):
            continue
        handle, title = product["handle"], product.get("title", "")
        if handle in seen:
            raise ValueError(
                f"two products share the handle {handle!r}: "
                f"{seen[handle]!r} and {title!r}"
            )
        seen[handle] = title
        rows.extend(product_rows(product))
        written += 1
        if limit and written >= limit:
            break
    return rows
