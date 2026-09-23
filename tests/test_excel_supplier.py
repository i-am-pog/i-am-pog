"""Reading Ace's cost list: an .xlsx with a cover sheet in front of the data."""

import unittest
from decimal import Decimal
from pathlib import Path

from bw.suppliers.spreadsheet import ExcelAdapter
from support import fixed_engine

FIXTURE = Path(__file__).parent / "fixtures" / "ace_cost.xlsx"

FIELDS = {
    "supplier_sku": ["BW SKU"],
    "title": ["Product"],
    "brand": ["Brand"],
    "cost": ["My Cost"],
    "msrp": ["Retail (ref)"],
    "size": ["Size / Variant"],
}


def build_fixture():
    import openpyxl
    workbook = openpyxl.Workbook()
    cover = workbook.active
    cover.title = "Read Me"
    cover["A1"] = "BRANDS WAREHOUSE"
    cover["A2"] = "Dropship Cost List - INTERNAL"

    sheet = workbook.create_sheet("Dropship Cost List")
    sheet.append(["BW SKU", "Brand", "Product", "Size / Variant", "Supplier",
                  "My Cost", "Base Price", "Ace Freight Adj", "My Price", "Margin",
                  "Retail (ref)"])
    sheet.append(["BW42654185", "24 ScentStory", "24 Elixir Gold For Man/Woman",
                  "Scentstory 24 Elixir Gold EDP M 100ml Boxed", "Ace",
                  46.84, 58.55, 0, 58.55, 0.2, 125])
    sheet.append(["BW00131808", "Al Haramain", "Amber Oud Gold For Man/Woman",
                  "Al Haramain Amber Oud EDP M 60ml Boxed", "Ace",
                  158, 197.5, -11.3, 186.2, 0.2, 196])
    sheet.append([None] * 11)                       # blank row mid-sheet
    sheet.append(["BW06250001", "Adidas", "Adidas Team Force For Man",
                  "Adidas Team Force EDT M 100ml Boxed", "Ace",
                  6.25, 7.81, 0, 7.81, 0.2, 22])
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(FIXTURE)


class ExcelSupplierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_fixture()

    def adapter(self, **extra):
        return ExcelAdapter("ace", {
            "path": str(FIXTURE), "sheet": "Dropship Cost List",
            "fields": FIELDS, **extra,
        })

    def test_reads_past_the_cover_sheet(self):
        items = self.adapter().fetch()
        self.assertEqual(len(items), 3)

    def test_uses_my_cost_not_my_price(self):
        # My Price is what we charge our own dropship customers. Using it as
        # cost would silently stack our margin on top of theirs.
        item = next(i for i in self.adapter().fetch() if i.supplier_sku == "BW42654185")
        self.assertEqual(item.cost, Decimal("46.84"))
        self.assertNotEqual(item.cost, Decimal("58.55"))

    def test_retail_reference_becomes_the_msrp(self):
        item = next(i for i in self.adapter().fetch() if i.supplier_sku == "BW42654185")
        self.assertEqual(item.msrp, Decimal("125"))

    def test_size_comes_from_the_variant_column(self):
        item = next(i for i in self.adapter().fetch() if i.supplier_sku == "BW00131808")
        self.assertEqual(item.size_ml, 60)
        self.assertEqual(item.concentration, "EDP")

    def test_blank_rows_are_skipped(self):
        self.assertTrue(all(i.supplier_sku for i in self.adapter().fetch()))

    def test_a_list_with_no_stock_column_uses_the_holding_quantity(self):
        items = self.adapter(assume_qty=3).fetch()
        self.assertTrue(all(i.qty == 3 for i in items))
        self.assertTrue(all(i.in_stock for i in items))

    def test_without_a_holding_quantity_nothing_reads_as_in_stock(self):
        self.assertTrue(all(not i.in_stock for i in self.adapter().fetch()))

    def test_the_header_row_is_found_rather_than_assumed(self):
        source = ExcelAdapter("ace", {"path": str(FIXTURE), "fields": FIELDS})
        self.assertEqual(len(source.fetch()), 3, "should pick the widest sheet")

    def test_a_missing_file_says_which_file(self):
        source = ExcelAdapter("ace", {"path": "data/not_here.xlsx", "fields": FIELDS})
        with self.assertRaises(FileNotFoundError) as caught:
            source.fetch()
        self.assertIn("not_here.xlsx", str(caught.exception))


class AceEconomicsTests(unittest.TestCase):
    """The pricing consequences of Ace's real numbers."""

    @classmethod
    def setUpClass(cls):
        build_fixture()

    def setUp(self):
        # Pinned to the strict policy (whole fee per item), since these test
        # what the fee does to an item, not what today's fee split is.
        self.engine = fixed_engine()
        self.items = {i.supplier_sku: i for i in ExcelAdapter("ace", {
            "path": str(FIXTURE), "sheet": "Dropship Cost List",
            "fields": FIELDS, "assume_qty": 3,
        }).fetch()}

    def test_a_cheap_bottle_cannot_be_dropshipped(self):
        # $6.25 cost, $22 retail. The $15 fee puts the floor near twice retail.
        item = self.items["BW06250001"]
        quote = self.engine.quote("ace", item.cost, None, item.msrp)
        self.assertFalse(quote.sellable)
        self.assertIn("above_retail", quote.flags)
        self.assertGreater(quote.floor_price, item.msrp)

    def test_the_same_bottle_works_when_we_stock_it(self):
        item = self.items["BW06250001"]
        quote = self.engine.quote("ace_stocked", item.cost, None, item.msrp)
        self.assertTrue(quote.sellable)
        self.assertLess(quote.price, item.msrp)
        self.assertGreater(quote.unit_profit, Decimal("0"))

    def test_a_thin_line_fails_however_we_ship_it(self):
        # $158 cost against $196 retail is 81% -- there is no room for our
        # margin, fee or no fee. Being expensive does not rescue a thin line;
        # only the cost-to-retail ratio does.
        item = self.items["BW00131808"]
        self.assertGreater(item.cost / item.msrp, Decimal("0.8"))
        for supplier in ("ace", "ace_stocked"):
            quote = self.engine.quote(supplier, item.cost, None, item.msrp)
            self.assertFalse(quote.sellable, f"{supplier} should hold this back")
            self.assertIn("above_retail", quote.flags)

    def test_the_gate_is_the_cost_to_retail_ratio(self):
        # Same retail, two costs. The fee is zero at this level either way, so
        # the ratio is the only thing deciding it.
        retail = Decimal("200")
        healthy = self.engine.quote("ace", Decimal("88"), None, retail)    # 44%
        thin = self.engine.quote("ace", Decimal("162"), None, retail)      # 81%
        self.assertTrue(healthy.sellable)
        self.assertFalse(thin.sellable)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class BulkExportTests(unittest.TestCase):
    """Reading a Shopify bulk export, which interleaves products and variants."""

    @classmethod
    def setUpClass(cls):
        import json, tempfile
        cls.path = Path(tempfile.mkdtemp()) / "bulk.jsonl"
        lines = [
            {"id": "gid://shopify/Product/1", "title": "Lattafa Asad", "vendor": "Lattafa",
             "status": "ACTIVE", "handle": "asad",
             "featuredMedia": {"preview": {"image": {"url": "https://cdn/asad.jpg"}}}},
            {"id": "gid://shopify/ProductVariant/11", "__parentId": "gid://shopify/Product/1",
             "sku": "A1", "barcode": "6291108730324", "title": "100ml", "price": "64.99",
             "inventoryQuantity": 0, "inventoryItem": {"id": "gid://shopify/InventoryItem/111"}},
            {"id": "gid://shopify/Product/2", "title": "Orphan Product", "vendor": "X",
             "status": "DRAFT", "handle": "orphan"},
            {"id": "gid://shopify/ProductVariant/99", "__parentId": "gid://shopify/Product/404",
             "sku": "GONE", "title": "100ml", "price": "1.00", "inventoryQuantity": 0},
        ]
        cls.path.write_text("".join(json.dumps(line) + "\n" for line in lines))

    def test_variants_are_joined_to_their_product(self):
        from bw.shopify import ShopifyClient
        variants = ShopifyClient.parse_bulk_catalog(self.path)
        self.assertEqual(len(variants), 1, "the orphaned variant must be dropped")
        variant = variants[0]
        self.assertEqual(variant.product_title, "Lattafa Asad")
        self.assertEqual(variant.vendor, "Lattafa")
        self.assertEqual(variant.status, "ACTIVE")
        self.assertEqual(variant.inventory_item_id, "gid://shopify/InventoryItem/111")
        self.assertEqual(variant.inventory_qty, 0)

    def test_product_images_are_collected(self):
        from bw.shopify import ShopifyClient
        images = ShopifyClient.bulk_product_images(self.path)
        self.assertEqual(images["gid://shopify/Product/1"], ["https://cdn/asad.jpg"])
        self.assertNotIn("gid://shopify/Product/2", images)


class DryRunTests(unittest.TestCase):
    def test_a_dry_run_needs_no_credentials(self):
        # A dry run that demands a location id cannot be used to check a plan
        # before the store is wired up, which is exactly when it is needed.
        from bw.shopify import ShopifyClient
        client = ShopifyClient(store="", token="", location_id="", dry_run=True)
        client.set_inventory([("gid://shopify/InventoryItem/1", 3)])
        self.assertEqual(len(client.calls), 1)

    def test_a_live_run_still_demands_one(self):
        from bw.shopify import ShopifyClient, ShopifyError
        client = ShopifyClient(store="s", token="t", location_id="", dry_run=False)
        with self.assertRaises(ShopifyError):
            client.set_inventory([("gid://shopify/InventoryItem/1", 3)])
