"""Supplier adapters.

An adapter's whole job is to hand back a list of SupplierItem. How it gets
them -- an API, a spreadsheet, a portal export -- stays its own business.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional, Protocol

from ..models import SupplierItem
from ..normalize import (
    is_gift_set,
    is_tester,
    parse_concentration,
    parse_gender,
    parse_size,
)

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def expand_env(value: Any) -> Any:
    """Resolve ${ENV_VAR} placeholders so credentials live in the environment."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def dig(payload: Any, path: str, default: Any = None) -> Any:
    """Follow a dotted path into nested JSON: "data.items" -> payload['data']['items']."""
    if not path:
        return payload
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)] if int(part) < len(current) else None
        else:
            return default
        if current is None:
            return default
    return current


def clean_money(value: Any) -> Optional[str]:
    """Pull a number out of "$29.89", "29,89", " 29.89 CAD"."""
    if value is None or value == "":
        return None
    text = str(value).strip().replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace("$", ""))
    return match.group(0) if match else None


def clean_qty(value: Any) -> int:
    if value is None or value == "":
        return 0
    text = str(value).strip().lower()
    if text in ("in stock", "available", "yes", "y", "true"):
        return 1          # stocked, count unknown -- enough to list it
    if text in ("out of stock", "no", "n", "false", "-"):
        return 0
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group(0)) if match else 0


def build_item(
    supplier: str,
    raw: dict[str, Any],
    mapping: dict[str, str],
) -> Optional[SupplierItem]:
    """Apply a field mapping to one raw row and derive the fragrance attributes."""

    def field(name: str) -> Any:
        source = mapping.get(name)
        if not source:
            return None
        if isinstance(source, list):
            for candidate in source:
                value = dig(raw, candidate) if "." in candidate else raw.get(candidate)
                if value not in (None, ""):
                    return value
            return None
        return dig(raw, source) if "." in source else raw.get(source)

    title = str(field("title") or "").strip()
    sku = str(field("supplier_sku") or "").strip()
    if not title or not sku:
        return None

    brand = str(field("brand") or "").strip()
    size_source = str(field("size") or "")
    size_ml, size_label = parse_size(size_source or title)

    images = field("image_urls")
    if isinstance(images, str):
        images = [u.strip() for u in images.split(",") if u.strip()]
    elif isinstance(images, list):
        images = [str(u).strip() for u in images if str(u).strip()]
    else:
        images = []

    context = f"{title} {size_source} {field('gender') or ''} {field('concentration') or ''}"

    return SupplierItem(
        supplier=supplier,
        supplier_sku=sku,
        title=title,
        brand=brand,
        cost=clean_money(field("cost")),
        qty=clean_qty(field("qty")),
        barcode=str(field("barcode") or "") or None,
        msrp=clean_money(field("msrp")),
        size_ml=size_ml,
        size_label=size_label,
        concentration=str(field("concentration") or "") or parse_concentration(context),
        gender=str(field("gender") or "") or parse_gender(context),
        tester=is_tester(context),
        gift_set=is_gift_set(context),
        image_urls=images,
        description=str(field("description") or "").strip(),
        raw=raw,
    )


class SupplierAdapter(Protocol):
    name: str

    def fetch(self) -> list[SupplierItem]:
        ...
