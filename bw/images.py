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

import re

from rapidfuzz import fuzz, process

from .models import CatalogVariant, SupplierItem
from .normalize import clean_product_name, normalize_barcode, squash, variant_key


# A storefront with nothing to show still puts a file on the page: "image
# coming soon" artwork, the same handful of URLs repeated across hundreds of
# products. Taking one is worse than taking none -- no photo reads as a listing
# still being built, while a coming-soon card reads as a shop that does not
# have the thing.
PLACEHOLDER = re.compile(
    r"coming.?soon|comesoon|no.?image|image.?unavailable|placeholder"
    r"|default.?(image|product)|nophoto|not.?available",
    re.I,
)


def is_placeholder(url: str) -> bool:
    """Whether a URL is stock 'no photo yet' artwork rather than the product."""
    return bool(PLACEHOLDER.search(url.rsplit("/", 1)[-1]))


# Words that do not tell two bottles apart: the concentration, the audience,
# and anything too short to carry meaning.
_NOISE = {
    "edp", "edt", "edc", "parfum", "perfume", "cologne", "spray", "eau", "de",
    "pour", "for", "the", "and", "man", "men", "woman", "women", "unisex",
    "homme", "femme", "ladies", "his", "her", "him",
}


def _distinctive(name: str) -> frozenset:
    """The words that actually identify a bottle within its brand."""
    return frozenset(w for w in name.replace("|", " ").split()
                     if len(w) > 2 and w not in _NOISE)


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
        urls = [u for u in urls if u and not is_placeholder(u)]
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
        #
        # Inside a brand the danger is the flanker: "Jean Lowe Maitre" and
        # "Jean Lowe Fraiche" share every word but the one that identifies the
        # bottle, and score high enough to pass on text alone. So a fuzzy hit
        # also has to use the same distinctive words -- same set, no extras on
        # either side -- which is exactly what separates one flanker from its
        # siblings.
        candidates = self._by_brand.get(squash(item.brand)) or []
        if candidates:
            wanted = _distinctive(blind)
            for name, score, _ in process.extract(blind, candidates, scorer=fuzz.WRatio,
                                                  score_cutoff=min_score, limit=10):
                if _distinctive(name) == wanted:
                    urls, source = self._names[name]
                    return ImageMatch(item, urls, source, score)

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
