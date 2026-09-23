"""Finding a photo for every listing.

The cost list Ace sends has no images, and a listing without one does not sell.
The pictures do exist though -- on Ace's own storefront, and on products we
already carry -- so this matches items to image URLs rather than going looking
for files.

The useful part: Shopify fetches image URLs itself when the product is created.
The bytes never pass through here, so an image source this machine cannot reach
is still usable, as long as Shopify's servers can reach it. What we need is the
URL, not the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from rapidfuzz import fuzz, process

from .models import CatalogVariant, SupplierItem
from .normalize import clean_product_name, normalize_barcode, squash, variant_key


def image_key(brand: str, title: str, size_ml: Optional[int]) -> str:
    name = clean_product_name(title, brand)
    return variant_key(brand, name, size_ml)


@dataclass
class ImageMatch:
    item: SupplierItem
    urls: list[str] = field(default_factory=list)
    source: str = ""
    score: float = 0.0

    @property
    def found(self) -> bool:
        return bool(self.urls)


class ImageLibrary:
    """Every image URL we know of, keyed so a cost-list row can find it."""

    def __init__(self):
        self._by_barcode: dict[str, tuple[list[str], str]] = {}
        self._by_key: dict[str, tuple[list[str], str]] = {}
        self._by_brand: dict[str, list[str]] = {}
        self._names: dict[str, tuple[list[str], str]] = {}

    def add(self, brand: str, title: str, size_ml: Optional[int],
            urls: Iterable[str], source: str, barcode: Optional[str] = None) -> None:
        urls = [u for u in urls if u]
        if not urls:
            return
        code = normalize_barcode(barcode)
        if code:
            self._by_barcode.setdefault(code, (urls, source))
        self._by_key.setdefault(image_key(brand, title, size_ml), (urls, source))

        # A photo of the 100ml is still the right photo for the 50ml, so keep a
        # size-blind fallback too.
        name = squash(clean_product_name(title, brand))
        blind = f"{squash(brand)}|{name}"
        self._names.setdefault(blind, (urls, source))
        self._by_brand.setdefault(squash(brand), []).append(blind)

    def add_supplier_items(self, items: Iterable[SupplierItem], source: str) -> None:
        for item in items:
            self.add(item.brand, item.title, item.size_ml, item.image_urls,
                     source, item.barcode)

    def add_catalog(self, variants: Iterable[CatalogVariant],
                    images: dict[str, list[str]], source: str = "our catalogue") -> None:
        """`images` maps product id to the URLs already on that product."""
        for variant in variants:
            urls = images.get(variant.product_id) or []
            if urls:
                self.add(variant.vendor, variant.product_title, None, urls,
                         source, variant.barcode)

    def __len__(self) -> int:
        return len(self._by_key) + len(self._names)

    def find(self, item: SupplierItem, min_score: float = 88.0) -> ImageMatch:
        code = normalize_barcode(item.barcode)
        if code and code in self._by_barcode:
            urls, source = self._by_barcode[code]
            return ImageMatch(item, urls, source, 100.0)

        key = image_key(item.brand, item.title, item.size_ml)
        if key in self._by_key:
            urls, source = self._by_key[key]
            return ImageMatch(item, urls, source, 100.0)

        # Same scent, any size.
        name = squash(clean_product_name(item.title, item.brand))
        blind = f"{squash(item.brand)}|{name}"
        if blind in self._names:
            urls, source = self._names[blind]
            return ImageMatch(item, urls, source, 99.0)

        # Within the same brand only -- across brands a fuzzy name match would
        # put the wrong bottle on the page, which is worse than no photo.
        candidates = self._by_brand.get(squash(item.brand)) or []
        if candidates:
            best = process.extractOne(blind, candidates, scorer=fuzz.WRatio,
                                      score_cutoff=min_score)
            if best:
                urls, source = self._names[best[0]]
                return ImageMatch(item, urls, source, best[1])

        return ImageMatch(item, [], "", 0.0)


def attach_images(items: Iterable[SupplierItem], library: ImageLibrary,
                  min_score: float = 88.0) -> tuple[list[ImageMatch], list[SupplierItem]]:
    """Fill in image_urls where we can. Returns (matches, still missing)."""
    matches, missing = [], []
    for item in items:
        match = library.find(item, min_score)
        if match.found:
            item.image_urls = match.urls
            matches.append(match)
        else:
            missing.append(item)
    return matches, missing
