"""Deciding whether a supplier item is already in our catalog.

Getting this wrong is expensive in both directions: a missed match creates a
duplicate listing competing with itself, and a false match overwrites a real
product's price and stock with another product's numbers. So matching runs
strongest-signal-first and anything doubtful goes to a review file rather than
being guessed at.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable, Optional

from rapidfuzz import fuzz, process

from .models import CatalogVariant, MatchResult, SupplierItem
from .normalize import (
    clean_product_name,
    normalize_barcode,
    parse_concentration,
    parse_gender,
    parse_size,
    squash,
    variant_key,
)

# A fuzzy pair at or above this, on the same size, is called a match.
AUTO_MATCH = 93.0
# Below this we do not even mention it.
REVIEW_FLOOR = 86.0


def variant_fingerprint(variant: CatalogVariant) -> tuple[str, Optional[int]]:
    """Reduce a Shopify variant to the same key shape we build for feed items."""
    size_ml, _ = parse_size(variant.title)
    if size_ml is None:
        size_ml, _ = parse_size(variant.product_title)

    brand = variant.vendor or ""
    name = clean_product_name(variant.product_title, brand)
    concentration = parse_concentration(variant.product_title, variant.title)
    gender = parse_gender(variant.product_title, variant.title)
    tester = "tester" in squash(f"{variant.product_title} {variant.title}")
    return variant_key(brand, name, size_ml, concentration, gender, tester), size_ml


def item_fingerprint(item: SupplierItem) -> str:
    name = clean_product_name(item.title, item.brand)
    return variant_key(
        item.brand, name, item.size_ml, item.concentration, item.gender, item.tester
    )


def search_text(brand: str, title: str, size_ml: Optional[int]) -> str:
    return squash(f"{brand} {clean_product_name(title, brand)} {size_ml or ''}")


class CatalogIndex:
    """Lookup structures over everything already in the store."""

    def __init__(self, variants: Iterable[CatalogVariant]):
        self.variants: list[CatalogVariant] = list(variants)
        self.by_barcode: dict[str, CatalogVariant] = {}
        self.by_sku: dict[str, CatalogVariant] = {}
        self.by_supplier_sku: dict[tuple[str, str], CatalogVariant] = {}
        self.by_key: dict[str, CatalogVariant] = {}
        self.ambiguous_barcodes: set[str] = set()

        # A barcode that shows up on several different products is a supplier
        # placeholder, not an identifier. Matching on it would merge unrelated
        # bottles, so those are struck out entirely.
        barcode_products: dict[str, set[str]] = defaultdict(set)
        for variant in self.variants:
            code = normalize_barcode(variant.barcode)
            if code:
                barcode_products[code].add(variant.product_id)
        self.ambiguous_barcodes = {c for c, ids in barcode_products.items() if len(ids) > 1}

        self._choices: list[str] = []
        self._choice_variants: list[CatalogVariant] = []
        self._by_brand: dict[str, list[int]] = defaultdict(list)

        for variant in self.variants:
            code = normalize_barcode(variant.barcode)
            if code and code not in self.ambiguous_barcodes:
                self.by_barcode.setdefault(code, variant)
            if variant.sku:
                self.by_sku.setdefault(variant.sku.strip().upper(), variant)
            if variant.supplier and variant.supplier_sku:
                self.by_supplier_sku.setdefault(
                    (variant.supplier.lower(), variant.supplier_sku.strip().upper()), variant
                )
            key, size_ml = variant_fingerprint(variant)
            self.by_key.setdefault(key, variant)
            self._by_brand[squash(variant.vendor)].append(len(self._choices))
            self._choices.append(search_text(variant.vendor, variant.product_title, size_ml))
            self._choice_variants.append(variant)

        self._brands = [b for b in self._by_brand if b]

    def __len__(self) -> int:
        return len(self.variants)

    def _brand_candidates(self, brand: str) -> dict[int, str]:
        """Listings for this brand, allowing for the feed spelling it differently."""
        key = squash(brand)
        indices = self._by_brand.get(key)
        if not indices and key and self._brands:
            near = process.extractOne(key, self._brands, scorer=fuzz.ratio, score_cutoff=88)
            if near:
                indices = self._by_brand[near[0]]
        return {i: self._choices[i] for i in (indices or [])}

    def match(self, item: SupplierItem) -> MatchResult:
        # 1. We already sourced this exact item from this supplier.
        key = (item.supplier.lower(), (item.supplier_sku or "").strip().upper())
        if key[1] and key in self.by_supplier_sku:
            return MatchResult(item, self.by_supplier_sku[key], "supplier_sku", 100.0)

        # 2. Barcode -- the only truly global identifier, when it is trustworthy.
        code = normalize_barcode(item.barcode)
        if code and code in self.by_barcode:
            return MatchResult(item, self.by_barcode[code], "barcode", 100.0)

        # 3. The supplier's own SKU happens to be our SKU.
        sku = (item.supplier_sku or "").strip().upper()
        if sku and sku in self.by_sku:
            return MatchResult(item, self.by_sku[sku], "sku", 100.0)

        # 4. Same brand, product, size, concentration, gender and packaging.
        fingerprint = item_fingerprint(item)
        if fingerprint in self.by_key:
            return MatchResult(item, self.by_key[fingerprint], "key", 100.0)

        # 5. Nothing exact. Compare against this brand's listings only --
        #    across brands, fuzzy scores are noise ("Lattafa Asad 100" scores
        #    respectably against "Carolina Herrera 212 100" on shared digits).
        candidates = self._brand_candidates(item.brand)
        if not candidates:
            return MatchResult(item, None, "none", 0.0)

        query = search_text(item.brand, item.title, item.size_ml)
        best = process.extractOne(query, candidates, scorer=fuzz.WRatio,
                                  score_cutoff=REVIEW_FLOOR)
        if not best:
            return MatchResult(item, None, "none", 0.0)

        _, score, index = best
        candidate = self._choice_variants[index]
        _, candidate_size = variant_fingerprint(candidate)
        same_size = candidate_size == item.size_ml

        # A strong text match on a different size is a different product, not a
        # match -- 50ml and 100ml of the same scent are separate variants.
        if score >= AUTO_MATCH and same_size:
            return MatchResult(item, candidate, "key", score)
        return MatchResult(item, candidate, "fuzzy", score)


def partition(items: Iterable[SupplierItem], index: CatalogIndex) -> dict[str, list[MatchResult]]:
    """Split a feed into what to create, what to update, and what to eyeball."""
    buckets: dict[str, list[MatchResult]] = {"new": [], "existing": [], "review": []}
    for item in items:
        result = index.match(item)
        if result.needs_review:
            buckets["review"].append(result)
        elif result.is_new:
            buckets["new"].append(result)
        else:
            buckets["existing"].append(result)
    return buckets


def duplicate_barcodes(items: Iterable[SupplierItem]) -> set[str]:
    """Barcodes a feed reuses across products -- unusable for matching."""
    counts = Counter()
    seen: dict[str, set[str]] = defaultdict(set)
    for item in items:
        code = normalize_barcode(item.barcode)
        if code:
            seen[code].add(squash(item.title))
    for code, titles in seen.items():
        if len(titles) > 1:
            counts[code] = len(titles)
    return set(counts)
