"""Shopify Admin GraphQL client.

Only the handful of operations this pipeline needs: read the catalog, create a
product with its variants and images, and push price/cost/stock updates. Every
write goes through `dry_run` so a run can be inspected before it touches the
live store.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import requests

from .models import CatalogVariant

CATALOG_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "catalog.jsonl"

# Where we record which supplier a variant came from, so a sync can find its
# own rows again and never touch anyone else's.
SUPPLY_NAMESPACE = "supply"


class ShopifyError(RuntimeError):
    pass


class ShopifyClient:
    def __init__(
        self,
        store: Optional[str] = None,
        token: Optional[str] = None,
        api_version: Optional[str] = None,
        location_id: Optional[str] = None,
        dry_run: bool = True,
        session: Optional[requests.Session] = None,
    ):
        self.store = store or os.environ.get("SHOPIFY_STORE", "")
        self.token = token or os.environ.get("SHOPIFY_ADMIN_TOKEN", "")
        self.api_version = api_version or os.environ.get("SHOPIFY_API_VERSION", "2025-07")
        self.location_id = location_id or os.environ.get("SHOPIFY_LOCATION_ID", "")
        self.dry_run = dry_run
        self.session = session or requests.Session()
        self.calls: list[dict] = []          # what a dry run would have sent

    @property
    def endpoint(self) -> str:
        return f"https://{self.store}/admin/api/{self.api_version}/graphql.json"

    # ---------------------------------------------------------------- transport

    def execute(self, query: str, variables: Optional[dict] = None,
                is_mutation: bool = False) -> dict:
        if is_mutation and self.dry_run:
            self.calls.append({"query": query, "variables": variables or {}})
            return {"_dry_run": True}

        if not self.store or not self.token:
            raise ShopifyError(
                "SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN must be set (see .env.example)"
            )

        for attempt in range(5):
            response = self.session.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
                headers={
                    "X-Shopify-Access-Token": self.token,
                    "Content-Type": "application/json",
                },
                timeout=60,
            )
            if response.status_code == 429:               # throttled
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                # Query cost throttling comes back as a GraphQL error, not a 429.
                if any("THROTTLED" in str(e) for e in payload["errors"]):
                    time.sleep(2 ** attempt)
                    continue
                raise ShopifyError(json.dumps(payload["errors"], indent=2))
            return payload["data"]

        raise ShopifyError("gave up after repeated throttling")

    @staticmethod
    def _raise_user_errors(result: dict, field: str) -> None:
        errors = (result or {}).get(field, {}).get("userErrors") or []
        if errors:
            raise ShopifyError(json.dumps(errors, indent=2))

    # ------------------------------------------------------------------ reading

    CATALOG_QUERY = """
    query Catalog($cursor: String) {
      products(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id title vendor status handle
          supplier: metafield(namespace: "supply", key: "supplier") { value }
          variants(first: 100) {
            nodes {
              id sku barcode title price
              supplierSku: metafield(namespace: "supply", key: "supplier_sku") { value }
              inventoryQuantity
              inventoryItem { id unitCost { amount } }
            }
          }
        }
      }
    }
    """

    def iter_catalog(self) -> Iterator[CatalogVariant]:
        """Every variant in the store, one page at a time."""
        cursor = None
        while True:
            data = self.execute(self.CATALOG_QUERY, {"cursor": cursor})
            block = data["products"]
            for product in block["nodes"]:
                supplier = (product.get("supplier") or {}).get("value", "") or ""
                for variant in product["variants"]["nodes"]:
                    item = variant.get("inventoryItem") or {}
                    cost = (item.get("unitCost") or {}).get("amount")
                    yield CatalogVariant(
                        variant_id=variant["id"],
                        product_id=product["id"],
                        sku=variant.get("sku") or "",
                        barcode=variant.get("barcode") or "",
                        title=variant.get("title") or "",
                        product_title=product.get("title") or "",
                        vendor=product.get("vendor") or "",
                        price=variant.get("price"),
                        cost=cost,
                        inventory_item_id=item.get("id", ""),
                        inventory_qty=variant.get("inventoryQuantity") or 0,
                        status=product.get("status") or "",
                        supplier=supplier,
                        supplier_sku=(variant.get("supplierSku") or {}).get("value", "") or "",
                    )
            if not block["pageInfo"]["hasNextPage"]:
                return
            cursor = block["pageInfo"]["endCursor"]

    def export_catalog(self, path: Path = CATALOG_CACHE) -> int:
        """Snapshot the catalog to disk so planning runs offline and fast."""
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w") as handle:
            for variant in self.iter_catalog():
                handle.write(json.dumps(variant.__dict__, default=str) + "\n")
                count += 1
        return count

    @staticmethod
    def load_catalog(path: Path = CATALOG_CACHE) -> list[CatalogVariant]:
        if not path.exists():
            raise FileNotFoundError(
                f"no catalog snapshot at {path} -- run `python -m bw.cli pull-catalog` first"
            )
        variants = []
        with path.open() as handle:
            for line in handle:
                if line.strip():
                    variants.append(CatalogVariant(**json.loads(line)))
        return variants

    # ------------------------------------------------------------------ writing

    PRODUCT_SET = """
    mutation Create($input: ProductSetInput!) {
      productSet(input: $input, synchronous: true) {
        product { id handle title variants(first: 50) { nodes { id sku } } }
        userErrors { field message }
      }
    }
    """

    def create_product(self, product_input: dict) -> dict:
        result = self.execute(self.PRODUCT_SET, {"input": product_input}, is_mutation=True)
        if result.get("_dry_run"):
            return result
        self._raise_user_errors(result, "productSet")
        return result["productSet"]["product"]

    VARIANTS_UPDATE = """
    mutation Prices($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
      productVariantsBulkUpdate(productId: $productId, variants: $variants) {
        productVariants { id price compareAtPrice }
        userErrors { field message }
      }
    }
    """

    def update_prices(self, product_id: str, variants: list[dict]) -> dict:
        result = self.execute(
            self.VARIANTS_UPDATE,
            {"productId": product_id, "variants": variants},
            is_mutation=True,
        )
        if result.get("_dry_run"):
            return result
        self._raise_user_errors(result, "productVariantsBulkUpdate")
        return result["productVariantsBulkUpdate"]

    INVENTORY_SET = """
    mutation Stock($input: InventorySetQuantitiesInput!) {
      inventorySetQuantities(input: $input) {
        inventoryAdjustmentGroup { createdAt reason }
        userErrors { field message }
      }
    }
    """

    def set_inventory(self, quantities: list[tuple[str, int]], reason: str = "correction") -> dict:
        """quantities: (inventory_item_id, on-hand count) pairs."""
        if not self.location_id:
            raise ShopifyError("SHOPIFY_LOCATION_ID must be set to write stock levels")
        payload = {
            "name": "available",
            "reason": reason,
            "ignoreCompareQuantity": True,
            "quantities": [
                {"inventoryItemId": item_id, "locationId": self.location_id, "quantity": qty}
                for item_id, qty in quantities
            ],
        }
        result = self.execute(self.INVENTORY_SET, {"input": payload}, is_mutation=True)
        if result.get("_dry_run"):
            return result
        self._raise_user_errors(result, "inventorySetQuantities")
        return result["inventorySetQuantities"]

    INVENTORY_ITEM_UPDATE = """
    mutation Cost($id: ID!, $input: InventoryItemInput!) {
      inventoryItemUpdate(id: $id, input: $input) {
        inventoryItem { id unitCost { amount } }
        userErrors { field message }
      }
    }
    """

    def update_cost(self, inventory_item_id: str, cost) -> dict:
        result = self.execute(
            self.INVENTORY_ITEM_UPDATE,
            {"id": inventory_item_id, "input": {"cost": str(cost)}},
            is_mutation=True,
        )
        if result.get("_dry_run"):
            return result
        self._raise_user_errors(result, "inventoryItemUpdate")
        return result["inventoryItemUpdate"]


def chunked(items: Iterable, size: int) -> Iterator[list]:
    batch: list = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
