"""The Shopify import CSV shape."""

import unittest

from bw.export import COLUMNS, plan_rows, product_rows


def product(handle="a-scent", variants=2, images=2, status="DRAFT"):
    return {
        "handle": handle,
        "title": "A Scent EDP for Man",
        "descriptionHtml": "<p>A Scent.</p>",
        "vendor": "Some House",
        "productType": "Fragrance",
        "status": status,
        "tags": ["Some House", "EDP"],
        "seo": {"title": "A Scent | Brands Warehouse", "description": "Shop A Scent."},
        "productOptions": [{"name": "Size", "position": 1}],
        "files": [{"originalSource": f"https://cdn/img{n}.jpg"} for n in range(images)],
        "variants": [
            {
                "optionValues": [{"optionName": "Size", "name": f"{50 + 50 * n}ml"}],
                "price": f"{80 + n}.95",
                "compareAtPrice": "125.00",
                "sku": f"SKU{n}",
                "inventoryPolicy": "DENY",
                "taxable": True,
                "inventoryItem": {"cost": "46.84",
                                  "measurement": {"weight": {"value": 460.0, "unit": "GRAMS"}}},
                "inventoryQuantities": [{"quantity": 3}],
            }
            for n in range(variants)
        ],
    }


class ImportCsvShape(unittest.TestCase):
    def test_each_variant_is_a_row_and_extra_images_get_their_own(self):
        rows = product_rows(product(variants=2, images=3))
        self.assertEqual(len(rows), 2 + 2)          # 2 variants + 2 extra images

    def test_only_the_first_row_carries_product_columns(self):
        rows = product_rows(product(variants=3, images=1))
        self.assertEqual(rows[0]["Title"], "A Scent EDP for Man")
        for row in rows[1:]:
            # Repeating these makes Shopify treat each row as its own product.
            self.assertEqual(row["Title"], "")
            self.assertEqual(row["Vendor"], "")
            self.assertEqual(row["Body (HTML)"], "")

    def test_every_row_repeats_the_handle(self):
        rows = product_rows(product(variants=2, images=3))
        self.assertTrue(all(r["Handle"] == "a-scent" for r in rows))

    def test_variant_columns_are_on_every_variant_row(self):
        rows = product_rows(product(variants=2, images=0))
        self.assertEqual([r["Variant SKU"] for r in rows], ["SKU0", "SKU1"])
        self.assertEqual([r["Option1 Value"] for r in rows], ["50ml", "100ml"])
        self.assertTrue(all(r["Variant Price"] for r in rows))

    def test_extra_image_rows_carry_nothing_but_handle_and_image(self):
        rows = product_rows(product(variants=1, images=3))
        extras = rows[1:]
        self.assertEqual([r["Image Position"] for r in extras], ["2", "3"])
        for row in extras:
            self.assertEqual(row["Variant SKU"], "")
            self.assertEqual(row["Title"], "")

    def test_nothing_is_published(self):
        rows = product_rows(product())
        self.assertEqual(rows[0]["Published"], "FALSE")
        self.assertEqual(rows[0]["Status"], "draft")

    def test_cost_and_weight_survive(self):
        row = product_rows(product())[0]
        self.assertEqual(row["Cost per item"], "46.84")
        self.assertEqual(row["Variant Grams"], "460")

    def test_a_weight_in_other_units_is_refused_rather_than_guessed(self):
        p = product()
        p["variants"][0]["inventoryItem"]["measurement"]["weight"]["unit"] = "POUNDS"
        with self.assertRaises(ValueError):
            product_rows(p)

    def test_rows_only_use_known_columns(self):
        for row in product_rows(product()):
            self.assertEqual(set(row) - set(COLUMNS), set())

    def test_with_images_only_skips_products_that_have_none(self):
        products = [product("with-photo", images=1), product("no-photo", images=0)]
        handles = {r["Handle"] for r in plan_rows(products, with_images_only=True)}
        self.assertEqual(handles, {"with-photo"})

    def test_limit_counts_products_not_rows(self):
        products = [product(f"p{n}", variants=2, images=2) for n in range(5)]
        handles = {r["Handle"] for r in plan_rows(products, limit=2)}
        self.assertEqual(len(handles), 2)

    def test_the_header_carries_all_three_option_columns(self):
        # Shopify rejects the whole file if Option2/Option3 Value are absent.
        for column in ("Option1 Value", "Option2 Value", "Option3 Value"):
            self.assertIn(column, COLUMNS)

    def test_two_products_under_one_handle_are_refused(self):
        # Shopify merges by handle, so this would silently collapse rather
        # than fail the import.
        products = [product("a-scent"), product("a-scent")]
        with self.assertRaises(ValueError):
            plan_rows(products)


if __name__ == "__main__":
    unittest.main()
