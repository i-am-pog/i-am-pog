"""Competitor prices from a Shopify storefront.

Shopify stores publish their catalog at /products.json -- the same data the
storefront renders, offered as JSON. That is the polite way to read a
competitor's prices: a public endpoint, fetched slowly, cached on disk, with
robots.txt checked first. No scraping of rendered pages, no logging in, no
pretending to be a browser.

FragranceBuy is the default target because it is the competitor named in the
store's own dev brief.
"""

from __future__ import annotations

import json
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import requests
from rapidfuzz import fuzz, process

from ..normalize import clean_product_name, parse_size, squash

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"
USER_AGENT = "BrandsWarehouse-PriceCheck/1.0 (+https://brandswarehouse.com)"


@dataclass
class MarketEntry:
    brand: str
    title: str
    size_ml: Optional[int]
    price: Decimal
    url: str = ""


class MarketIndex:
    """Lookup of competitor prices by brand, product and size."""

    def __init__(self, entries: list[MarketEntry]):
        self.entries = entries
        self._by_key: dict[tuple[str, str, Optional[int]], Decimal] = {}
        self._by_brand: dict[str, list[int]] = {}
        self._names: list[str] = []

        for index, entry in enumerate(entries):
            brand = squash(entry.brand)
            name = squash(clean_product_name(entry.title, entry.brand))
            key = (brand, name, entry.size_ml)
            # Several listings of the same thing: keep the cheapest, since that
            # is the price we actually have to beat.
            if key not in self._by_key or entry.price < self._by_key[key]:
                self._by_key[key] = entry.price
            self._by_brand.setdefault(brand, []).append(index)
            self._names.append(name)

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, brand: str, title: str, size_ml: Optional[int],
               min_score: float = 90.0) -> Optional[Decimal]:
        brand_key = squash(brand)
        name = squash(clean_product_name(title, brand))

        exact = self._by_key.get((brand_key, name, size_ml))
        if exact is not None:
            return exact

        candidates = {i: self._names[i] for i in self._by_brand.get(brand_key, [])}
        if not candidates:
            return None
        best = process.extractOne(name, candidates, scorer=fuzz.WRatio, score_cutoff=min_score)
        if not best:
            return None
        entry = self.entries[best[2]]
        # Same scent, wrong bottle size is the wrong price.
        return entry.price if entry.size_ml == size_ml else None


class ShopifyStoreMarket:
    def __init__(self, name: str = "fragrancebuy", domain: str = "fragrancebuy.ca",
                 delay_seconds: float = 1.0, max_pages: int = 100,
                 session: Optional[requests.Session] = None):
        self.name = name
        self.domain = domain.rstrip("/")
        self.delay_seconds = delay_seconds
        self.max_pages = max_pages
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.cache_path = CACHE_DIR / f"market_{name}.json"

    # ----------------------------------------------------------------- access

    def _allowed(self, path: str = "/products.json") -> bool:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(f"https://{self.domain}/robots.txt")
        try:
            parser.read()
        except Exception:
            return False          # cannot confirm we are welcome: do not fetch
        return parser.can_fetch(USER_AGENT, f"https://{self.domain}{path}")

    def refresh(self) -> list[MarketEntry]:
        """Pull the competitor's public catalog, one page a second."""
        if not self._allowed():
            raise PermissionError(f"{self.domain}/robots.txt disallows /products.json")

        entries: list[MarketEntry] = []
        for page in range(1, self.max_pages + 1):
            response = self.session.get(
                f"https://{self.domain}/products.json",
                params={"limit": 250, "page": page},
                timeout=60,
            )
            response.raise_for_status()
            products = response.json().get("products", [])
            if not products:
                break
            for product in products:
                vendor = product.get("vendor") or ""
                title = product.get("title") or ""
                handle = product.get("handle") or ""
                for variant in product.get("variants", []):
                    price = variant.get("price")
                    if price in (None, ""):
                        continue
                    size_ml, _ = parse_size(variant.get("title") or "")
                    if size_ml is None:
                        size_ml, _ = parse_size(title)
                    entries.append(MarketEntry(
                        brand=vendor,
                        title=title,
                        size_ml=size_ml,
                        price=Decimal(str(price)),
                        url=f"https://{self.domain}/products/{handle}",
                    ))
            time.sleep(self.delay_seconds)

        self._save(entries)
        return entries

    def load_file(self, path) -> MarketIndex:
        """Read a products.json someone saved from their own browser.

        The plainest way past a blocked network: open
        https://<store>/products.json?limit=250 in a browser, save the page,
        hand over the file. Same data the fetcher would have got.
        """
        from pathlib import Path as _Path
        payload = json.loads(_Path(path).read_text())
        products = payload.get("products", payload if isinstance(payload, list) else [])
        entries: list[MarketEntry] = []
        for product in products:
            vendor = product.get("vendor") or ""
            title = product.get("title") or ""
            handle = product.get("handle") or ""
            for variant in product.get("variants", []):
                price = variant.get("price")
                if price in (None, ""):
                    continue
                size_ml, _ = parse_size(variant.get("title") or "")
                if size_ml is None:
                    size_ml, _ = parse_size(title)
                entries.append(MarketEntry(
                    brand=vendor, title=title, size_ml=size_ml,
                    price=Decimal(str(price)),
                    url=f"https://{self.domain}/products/{handle}",
                ))
        self._save(entries)
        return MarketIndex(entries)

    # ------------------------------------------------------------------ cache

    def _save(self, entries: list[MarketEntry]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "domain": self.domain,
            "entries": [
                {"brand": e.brand, "title": e.title, "size_ml": e.size_ml,
                 "price": str(e.price), "url": e.url}
                for e in entries
            ],
        }))

    def load(self, max_age_hours: int = 24, refresh: bool = True) -> MarketIndex:
        """Cached prices, refetched when stale. Never blocks a run on the network."""
        if self.cache_path.exists():
            payload = json.loads(self.cache_path.read_text())
            fetched = datetime.fromisoformat(payload["fetched_at"])
            fresh = datetime.now(timezone.utc) - fetched < timedelta(hours=max_age_hours)
            if fresh or not refresh:
                return MarketIndex([
                    MarketEntry(e["brand"], e["title"], e["size_ml"],
                                Decimal(e["price"]), e.get("url", ""))
                    for e in payload["entries"]
                ])
        if not refresh:
            return MarketIndex([])
        return MarketIndex(self.refresh())


def empty_index() -> MarketIndex:
    """Used when no market data is available -- pricing then falls back to the floor."""
    return MarketIndex([])
