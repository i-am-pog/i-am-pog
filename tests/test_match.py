import unittest
from decimal import Decimal

from bw.match import CatalogIndex, partition
from bw.models import CatalogVariant, SupplierItem
from bw.normalize import parse_concentration, parse_gender, parse_size


def catalog():
    """Rows shaped like the real Brands Warehouse catalog."""
    return [
        CatalogVariant(variant_id="v1", product_id="p1", sku="BW79777",
                       barcode="8411061896259", title="50ml",
                       product_title="Carolina Herrera 212", vendor="Carolina Herrera"),
        CatalogVariant(variant_id="v2", product_id="p1", sku="BW82512",
                       barcode="8411061043882", title="100ml",
                       product_title="Carolina Herrera 212", vendor="Carolina Herrera"),
        CatalogVariant(variant_id="v3", product_id="p2", sku="ST-AVEN1626",
                       barcode="4010000000000", title="Kids / Kids",
                       product_title="Kids Avengers Set Bs 150 ML", vendor="Kids"),
        CatalogVariant(variant_id="v4", product_id="p3", sku="ST-SPID1671",
                       barcode="4010000000000", title="Kids / Kids",
                       product_title="Kids Spiderman Set Bs100 ML", vendor="Kids"),
        CatalogVariant(variant_id="v5", product_id="p4", sku="BW98517",
                       barcode="8411061974759", title="50ml",
                       product_title="Carolina Herrera 212 Heroes Forever Young",
                       vendor="Carolina Herrera", supplier="ace", supplier_sku="I0098517"),
    ]


def feed_item(title, brand, sku, barcode=None, cost="40"):
    size_ml, label = parse_size(title)
    return SupplierItem(
        supplier="ace", supplier_sku=sku, title=title, brand=brand, cost=cost, qty=5,
        barcode=barcode, size_ml=size_ml, size_label=label,
        concentration=parse_concentration(title), gender=parse_gender(title),
    )


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.index = CatalogIndex(catalog())

    def test_matches_on_barcode(self):
        item = feed_item("212 (M) EDT SP 1.7oz", "Carolina Herrera", "I0079777",
                         barcode="8411061896259")
        result = self.index.match(item)
        self.assertEqual(result.method, "barcode")
        self.assertEqual(result.variant.variant_id, "v1")

    def test_upc_missing_its_leading_zero_still_matches(self):
        index = CatalogIndex([CatalogVariant(variant_id="x", product_id="px",
                                             barcode="085435700359", title="240ml",
                                             product_title="Completely Bare Hand Sanitizer",
                                             vendor="Completely Bare")])
        item = feed_item("Completely Bare Hand Sanitizer 8.0oz", "Completely Bare", "I1",
                         barcode="85435700359")
        self.assertEqual(index.match(item).method, "barcode")

    def test_placeholder_barcode_never_matches(self):
        # 4010000000000 sits on two unrelated products in the catalog and is a
        # filler value besides. Either defence is enough; neither may match.
        item = feed_item("Some Other Kids Set 100ml", "Kids", "I5", barcode="4010000000000")
        self.assertNotEqual(self.index.match(item).method, "barcode")

    def test_a_barcode_reused_across_products_is_struck_out(self):
        shared = "8411061000017"
        index = CatalogIndex([
            CatalogVariant(variant_id="a", product_id="pa", barcode=shared, title="100ml",
                           product_title="Some Brand One", vendor="Some Brand"),
            CatalogVariant(variant_id="b", product_id="pb", barcode=shared, title="100ml",
                           product_title="Some Brand Two", vendor="Some Brand"),
        ])
        self.assertIn(shared, index.ambiguous_barcodes)
        item = feed_item("Some Brand Three EDP 100ml", "Some Brand", "I6", barcode=shared)
        self.assertNotEqual(index.match(item).method, "barcode")

    def test_matches_on_supplier_sku_even_when_barcode_is_missing(self):
        item = feed_item("212 HEROES FOREVER YOUNG(M)EDT SP 1.7oz", "Carolina Herrera",
                         "I0098517")
        result = self.index.match(item)
        self.assertEqual(result.method, "supplier_sku")
        self.assertEqual(result.variant.variant_id, "v5")

    def test_matches_on_attributes_when_there_is_no_identifier(self):
        item = feed_item("Carolina Herrera 212 Eau de Toilette 3.4 OZ for Men",
                         "Carolina Herrera", "I999")
        result = self.index.match(item)
        self.assertEqual(result.variant.variant_id, "v2")   # the 100ml
        self.assertIn(result.method, ("key",))

    def test_a_size_we_do_not_carry_is_new(self):
        item = feed_item("Carolina Herrera 212 EDT 6.7oz for Men", "Carolina Herrera", "I998")
        result = self.index.match(item)
        self.assertTrue(result.is_new or result.needs_review)
        self.assertNotEqual(result.method, "key")

    def test_unrelated_product_is_new(self):
        item = feed_item("Lattafa Asad EDP 100ml", "Lattafa", "I997")
        self.assertTrue(self.index.match(item).is_new)

    def test_partition_splits_the_feed(self):
        items = [
            feed_item("212 (M) EDT SP 1.7oz", "Carolina Herrera", "I1", "8411061896259"),
            feed_item("Lattafa Asad EDP 100ml", "Lattafa", "I2"),
        ]
        buckets = partition(items, self.index)
        self.assertEqual(len(buckets["existing"]), 1)
        self.assertEqual(len(buckets["new"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestersDoNotMatchRetailListings(unittest.TestCase):
    """A tester is cheaper and unboxed -- it must not price a retail listing."""

    def setUp(self):
        self.retail = CatalogVariant(
            variant_id="gid://shopify/ProductVariant/1",
            product_id="gid://shopify/Product/1",
            inventory_item_id="gid://shopify/InventoryItem/1",
            product_title="Escada Fairy Love For Women",
            title="Escada / 100 ML / Women",
            sku="FAIR100TS-W", vendor="Escada", barcode="",
            price=Decimal("105.00"), inventory_qty=0, status="ACTIVE",
        )
        self.index = CatalogIndex([self.retail])

    def item(self, title, tester):
        return SupplierItem(
            supplier="ace", supplier_sku="BW01789253", title=title,
            brand="Escada", cost=Decimal("23"), size_ml=100, gender="Women",
            tester=tester, qty=3,
        )

    def test_tester_does_not_auto_match_a_boxed_listing(self):
        result = self.index.match(self.item("Tester - Escada Fairy Love For Women", True))
        self.assertNotEqual(result.method, "key")
        self.assertTrue(result.needs_review)

    def test_the_boxed_bottle_still_matches(self):
        result = self.index.match(self.item("Escada Fairy Love For Women", False))
        self.assertEqual(result.variant, self.retail)
        self.assertFalse(result.needs_review)
