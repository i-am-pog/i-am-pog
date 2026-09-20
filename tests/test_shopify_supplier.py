"""The Ace adapter: a supplier that runs its own Shopify store."""

import json
import unittest
from decimal import Decimal
from pathlib import Path

from bw.pricing import PricingEngine
from bw.suppliers.shopify_store import ShopifyStoreAdapter, strip_html

FIXTURES = Path(__file__).parent / "fixtures"


def products():
    return json.loads((FIXTURES / "ace_products.json").read_text())["products"]


def adapter(**cost):
    return ShopifyStoreAdapter("ace", {
        "domain": "acegiftsplus.ca",
        "cost": cost or {"mode": "dealer_discount", "dealer_discount": 0.40},
        "qty": {"available_qty": 5},
    })


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.items = adapter().build_items(products())

    def test_every_variant_becomes_an_item(self):
        self.assertEqual(len(self.items), 5)

    def test_reads_brand_size_and_images(self):
        asad = next(i for i in self.items if i.supplier_sku == "LAT-ASAD-100")
        self.assertEqual(asad.brand, "Lattafa")
        self.assertEqual(asad.size_ml, 100)
        self.assertEqual(asad.concentration, "EDP")
        self.assertEqual(asad.gender, "Man")
        self.assertEqual(asad.image_urls, ["https://cdn.shopify.com/s/files/asad.jpg"])
        self.assertIn("Asad", asad.description)
        self.assertNotIn("<", asad.description, "HTML must be stripped")

    def test_their_retail_price_becomes_our_ceiling(self):
        asad = next(i for i in self.items if i.supplier_sku == "LAT-ASAD-100")
        self.assertEqual(asad.msrp, Decimal("64.99"))

    def test_we_are_never_listed_above_the_suppliers_own_shelf_price(self):
        # At a 40% dealer discount this bottle costs us $38.99, and the $15 fee
        # puts our floor above Ace's own $64.99. Listing it would make us the
        # expensive option, so it must be held back.
        asad = next(i for i in self.items if i.supplier_sku == "LAT-ASAD-100")
        quote = PricingEngine().quote("ace", asad.cost, None, asad.msrp)
        self.assertGreater(quote.floor_price, Decimal("64.99"))
        self.assertFalse(quote.sellable)
        self.assertIn("above_supplier_retail", quote.flags)

    def test_a_deep_enough_discount_makes_the_same_item_work(self):
        # The same bottle at 65% off retail clears the floor comfortably.
        items = adapter(mode="dealer_discount", dealer_discount=0.65).build_items(products())
        asad = next(i for i in items if i.supplier_sku == "LAT-ASAD-100")
        quote = PricingEngine().quote("ace", asad.cost, None, asad.msrp)
        self.assertTrue(quote.sellable)
        self.assertLess(quote.price, Decimal("64.99"))
        self.assertGreater(quote.unit_profit, Decimal("0"))

    def test_out_of_stock_variants_come_through_as_zero(self):
        small = next(i for i in self.items if i.supplier_sku == "LAT-ASAD-30")
        self.assertEqual(small.qty, 0)
        self.assertFalse(small.in_stock)

    def test_available_variants_get_the_assumed_count(self):
        asad = next(i for i in self.items if i.supplier_sku == "LAT-ASAD-100")
        self.assertEqual(asad.qty, 5)

    def test_real_quantities_are_used_when_the_store_exposes_them(self):
        source = ShopifyStoreAdapter("ace", {
            "domain": "acegiftsplus.ca",
            "cost": {"mode": "dealer_discount", "dealer_discount": 0.4},
            "qty": {"field": "inventory_quantity", "available_qty": 5},
        })
        raw = products()
        raw[0]["variants"][0]["inventory_quantity"] = 42
        item = next(i for i in source.build_items(raw) if i.supplier_sku == "LAT-ASAD-100")
        self.assertEqual(item.qty, 42)

    def test_shopifys_default_title_is_not_treated_as_a_size(self):
        mug = next(i for i in self.items if i.supplier_sku == "MUG-BOX")
        self.assertEqual(mug.title, "Ceramic Mug Gift Box")
        self.assertEqual(mug.size_label, "")
        self.assertTrue(mug.gift_set)

    def test_a_variant_without_a_sku_still_gets_a_stable_id(self):
        mystery = next(i for i in self.items if i.supplier_sku == "114-9115")
        self.assertTrue(mystery.supplier_sku)


class CostTests(unittest.TestCase):
    def test_dealer_discount_off_retail(self):
        items = adapter(mode="dealer_discount", dealer_discount=0.40).build_items(products())
        asad = next(i for i in items if i.supplier_sku == "LAT-ASAD-100")
        self.assertEqual(asad.cost, Decimal("38.99"))       # 64.99 less 40%

    def test_wholesale_price_list_wins_when_we_have_one(self):
        items = adapter(
            mode="price_list",
            price_list_path=str(FIXTURES / "ace_wholesale.csv"),
        ).build_items(products())
        asad = next(i for i in items if i.supplier_sku == "LAT-ASAD-100")
        self.assertEqual(asad.cost, Decimal("26.00"))

    def test_items_missing_from_the_price_list_are_not_guessed_at(self):
        items = adapter(
            mode="price_list",
            price_list_path=str(FIXTURES / "ace_wholesale.csv"),
        ).build_items(products())
        mystery = next(i for i in items if i.supplier_sku == "114-9115")
        self.assertIsNone(mystery.cost)
        self.assertFalse(mystery.sellable, "no cost means it must not be priced or listed")

    def test_no_cost_configured_means_nothing_is_sellable(self):
        items = adapter(mode="dealer_discount").build_items(products())
        self.assertTrue(all(not i.sellable for i in items))


class HtmlTests(unittest.TestCase):
    def test_strip_html_unescapes_and_trims(self):
        self.assertEqual(strip_html("<p>Tom &amp; Jerry</p>").strip(), "Tom & Jerry")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DealerSessionTests(unittest.TestCase):
    """Carrying a dealer login, and proving it actually does something."""

    def test_a_cookie_is_carried_on_requests(self):
        source = ShopifyStoreAdapter("ace", {
            "domain": "acegiftsplus.ca",
            "auth": {"cookie": "secure_customer_sig=abc123"},
            "cost": {"mode": "dealer_discount", "dealer_discount": 0.5},
        })
        self.assertTrue(source.authenticated)
        self.assertEqual(source.session.headers["Cookie"], "secure_customer_sig=abc123")

    def test_a_saved_browser_session_is_loaded(self):
        import json, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"cookies": [
                {"name": "secure_customer_sig", "value": "xyz", "domain": ".acegiftsplus.ca"},
                {"name": "other", "value": "nope", "domain": ".example.com"},
            ]}, handle)
            path = handle.name
        source = ShopifyStoreAdapter("ace", {
            "domain": "acegiftsplus.ca",
            "auth": {"storage_state": path},
            "cost": {"mode": "dealer_discount", "dealer_discount": 0.5},
        })
        self.assertTrue(source.authenticated)
        self.assertEqual(source.session.cookies.get("secure_customer_sig"), "xyz")
        self.assertIsNone(source.session.cookies.get("other"),
                          "cookies for other domains must not be sent to this one")

    def test_missing_session_file_is_not_fatal(self):
        source = ShopifyStoreAdapter("ace", {
            "domain": "acegiftsplus.ca",
            "auth": {"storage_state": "/nonexistent/session.json"},
            "cost": {"mode": "dealer_discount", "dealer_discount": 0.5},
        })
        self.assertFalse(source.authenticated)

    def test_no_auth_configured_reads_the_public_catalog(self):
        self.assertFalse(adapter().authenticated)


class SessionDescriptionTests(unittest.TestCase):
    """The pre-flight cookie check, which needs no network."""

    @staticmethod
    def described(cookie):
        from bw.cli import describe_session
        return " ".join(describe_session(ShopifyStoreAdapter("ace", {
            "domain": "acegiftsplus.ca",
            "auth": {"cookie": cookie} if cookie else {},
            "cost": {"mode": "dealer_discount", "dealer_discount": 0.5},
        })))

    def test_nothing_set(self):
        self.assertIn("NONE", self.described(None))

    def test_a_logged_in_cookie_is_recognised(self):
        text = self.described("_shopify_y=abc; secure_customer_sig=deadbeef; cart=xyz")
        self.assertIn("logged in", text)
        self.assertNotIn("!!", text)

    def test_a_logged_out_cookie_is_called_out(self):
        text = self.described("_shopify_y=abc; cart=xyz")
        self.assertIn("secure_customer_sig", text)
        self.assertIn("!!", text)

    def test_a_value_pasted_without_its_name_is_called_out(self):
        self.assertIn("no name=value pairs", self.described("justsomeopaquestring"))
