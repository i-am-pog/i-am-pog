#!/usr/bin/env python3
"""Pull wholesale prices from Ace using your own login -- on your machine.

Your password never leaves your computer and is never typed into this script.
It opens a real browser window, you log in yourself exactly as you normally
would (including any 2FA), and only the resulting session is saved locally, to
a file that is gitignored. Nothing is sent anywhere.

    pip install -r requirements-tools.txt
    playwright install chromium

    python tools/ace_session.py login       # a browser opens; log in; press Enter
    python tools/ace_session.py diagnose    # find where the wholesale prices are
    python tools/ace_session.py prices      # write data/ace_wholesale.csv

`diagnose` exists because a dealer login can expose wholesale pricing in
several different places depending on how the store is set up, and guessing
wrong gives you retail prices that look plausible. It compares what the site
shows logged out against what it shows logged in, and tells you which endpoint
actually carries your pricing -- or that none of them do, in which case the
answer is to ask Ace for a price list file instead.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
SESSION_PATH = ROOT / "data" / ".ace_session.json"
DEFAULT_DOMAIN = "acegiftsplus.ca"


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "This needs Playwright:\n"
            "    pip install -r requirements-tools.txt\n"
            "    playwright install chromium"
        )
    return sync_playwright


# ------------------------------------------------------------------- login

def cmd_login(args) -> int:
    sync_playwright = require_playwright()
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"https://{args.domain}/account/login", wait_until="domcontentloaded")

        print("\nA browser window is open.")
        print("  1. Log in there yourself, the way you always do.")
        print("  2. Navigate to a page where you can SEE your wholesale prices.")
        print("  3. Come back here and press Enter.\n")
        print("Your password is typed into the real site, not into this script,")
        print("and is never stored.\n")
        input("Press Enter once you are logged in and can see wholesale pricing... ")

        context.storage_state(path=str(SESSION_PATH))
        current = page.url
        browser.close()

    SESSION_PATH.chmod(0o600)
    print(f"\nsession saved to {SESSION_PATH} (gitignored, readable only by you)")
    print(f"last page you were on: {current}")
    print("\nnext:  python tools/ace_session.py diagnose")
    return 0


def load_context(playwright, domain: str, authenticated: bool):
    if authenticated and not SESSION_PATH.exists():
        sys.exit("no saved session -- run `python tools/ace_session.py login` first")
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=str(SESSION_PATH) if authenticated else None
    )
    return browser, context


def fetch_json(context, url: str) -> Optional[Any]:
    try:
        response = context.request.get(url, timeout=45000)
        if not response.ok:
            return None
        return response.json()
    except Exception:
        return None


def variant_prices(payload: Any) -> dict[str, str]:
    """{sku or variant id: price} out of a products.json or product.js payload."""
    products = payload.get("products") if isinstance(payload, dict) else None
    if products is None and isinstance(payload, dict) and "variants" in payload:
        products = [payload]
    prices: dict[str, str] = {}
    for product in products or []:
        for variant in product.get("variants", []):
            key = (variant.get("sku") or "").strip() or str(variant.get("id"))
            price = variant.get("price")
            if price is None:
                continue
            # products.json gives dollars as a string; product.js gives cents.
            if isinstance(price, int):
                price = f"{price / 100:.2f}"
            prices[key] = str(price)
    return prices


# ---------------------------------------------------------------- diagnose

def cmd_diagnose(args) -> int:
    sync_playwright = require_playwright()
    base = f"https://{args.domain}"

    with sync_playwright() as playwright:
        anon_browser, anon = load_context(playwright, args.domain, authenticated=False)
        auth_browser, auth = load_context(playwright, args.domain, authenticated=True)

        print(f"checking {args.domain} logged out vs logged in...\n")

        catalog = fetch_json(anon, f"{base}/products.json?limit=10")
        if not catalog or not catalog.get("products"):
            print("  /products.json returned nothing useful.")
            print("  The wholesale side may be a separate store or a password page.")
            print("  Log in, find the page that lists your prices, and pass its URL:")
            print("      python tools/ace_session.py diagnose --sample-url <url>")
            anon_browser.close(); auth_browser.close()
            return 1

        handles = [p["handle"] for p in catalog["products"][:args.sample]]
        print(f"  sampling {len(handles)} products\n")
        print(f"  {'product':<34}{'logged out':>12}{'logged in':>12}   source")

        findings = {"products.json": 0, "product.js": 0, "page": 0}
        for handle in handles:
            anon_prices = variant_prices(fetch_json(anon, f"{base}/products/{handle}.js") or {})
            auth_prices = variant_prices(fetch_json(auth, f"{base}/products/{handle}.js") or {})
            out = next(iter(anon_prices.values()), "-")
            inn = next(iter(auth_prices.values()), "-")
            if out != inn and inn != "-":
                findings["product.js"] += 1
                source = "product.js DIFFERS  <-- wholesale"
            else:
                # Same JSON either way: the price may only be rendered in HTML.
                page = auth.new_page()
                page.goto(f"{base}/products/{handle}", wait_until="domcontentloaded")
                time.sleep(args.delay)
                text = page.inner_text("body")[:4000]
                page.close()
                shown = _first_price(text)
                if shown and shown != out:
                    findings["page"] += 1
                    source = f"page shows {shown}  <-- wholesale, HTML only"
                    inn = shown
                else:
                    source = "same as logged out"
            print(f"  {handle[:33]:<34}{out:>12}{inn:>12}   {source}")

        anon_browser.close(); auth_browser.close()

    print()
    if findings["product.js"]:
        print(f"  -> your pricing is in the JSON. Run:  python tools/ace_session.py prices")
    elif findings["page"]:
        print(f"  -> your pricing is rendered in the page only. Run:")
        print(f"     python tools/ace_session.py prices --from-pages   (slower)")
    else:
        print("  -> logged in and logged out show the SAME prices.")
        print("     This account may not carry wholesale pricing on the storefront.")
        print("     Ask Ace for a price list file instead and use:")
        print("       python tools/parse_pricelist.py their-list.pdf -o data/ace_wholesale.csv")
    return 0


def _first_price(text: str) -> Optional[str]:
    import re
    match = re.search(r"\$\s*([\d,]+\.\d{2})", text)
    return match.group(1).replace(",", "") if match else None


# ------------------------------------------------------------------ prices

def cmd_prices(args) -> int:
    sync_playwright = require_playwright()
    base = f"https://{args.domain}"
    rows: list[dict] = []

    with sync_playwright() as playwright:
        browser, context = load_context(playwright, args.domain, authenticated=True)

        products, page_number = [], 1
        while page_number <= args.max_pages:
            payload = fetch_json(context, f"{base}/products.json?limit=250&page={page_number}")
            batch = (payload or {}).get("products", [])
            if not batch:
                break
            products.extend(batch)
            print(f"  page {page_number}: {len(batch)} products", end="\r")
            page_number += 1
            time.sleep(args.delay)
        print(f"  {len(products)} products listed            ")

        for index, product in enumerate(products, 1):
            handle = product.get("handle")
            if args.from_pages:
                page = context.new_page()
                page.goto(f"{base}/products/{handle}", wait_until="domcontentloaded")
                time.sleep(args.delay)
                price = _first_price(page.inner_text("body")[:4000])
                page.close()
                prices = {}
                for variant in product.get("variants", []):
                    key = (variant.get("sku") or "").strip() or str(variant.get("id"))
                    prices[key] = price
            else:
                prices = variant_prices(fetch_json(context, f"{base}/products/{handle}.js") or {})
                time.sleep(args.delay)

            for variant in product.get("variants", []):
                sku = (variant.get("sku") or "").strip() or str(variant.get("id"))
                cost = prices.get(sku)
                if not cost:
                    continue
                rows.append({
                    "sku": sku,
                    "barcode": variant.get("barcode") or "",
                    "brand": product.get("vendor", ""),
                    "title": f"{product.get('title','')} {variant.get('title','')}".strip(),
                    "type": product.get("product_type", ""),
                    "concentration": "",
                    "gender": "",
                    "size": variant.get("title", ""),
                    "qty": 5 if variant.get("available") else 0,
                    "cost": cost,
                    "retail": variant.get("price", ""),
                })
            if index % 25 == 0:
                print(f"  priced {index}/{len(products)}", end="\r")

        browser.close()

    if not rows:
        sys.exit("nothing priced -- run `diagnose` first to see where your prices live")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    args.out.chmod(0o600)

    print(f"\nwrote {len(rows)} priced variants -> {args.out}")
    print("next:  python -m bw.cli probe ace_pricelist")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    parser.add_argument("--delay", type=float, default=0.6,
                        help="pause between requests; be kind to their server")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="open a browser and save the session")
    login.set_defaults(func=cmd_login)

    diagnose = subparsers.add_parser("diagnose", help="find where your wholesale prices live")
    diagnose.add_argument("--sample", type=int, default=5)
    diagnose.add_argument("--sample-url", default="")
    diagnose.set_defaults(func=cmd_diagnose)

    prices = subparsers.add_parser("prices", help="write data/ace_wholesale.csv")
    prices.add_argument("-o", "--out", type=Path, default=ROOT / "data" / "ace_wholesale.csv")
    prices.add_argument("--from-pages", action="store_true",
                        help="read prices from rendered pages (slower, needed for some stores)")
    prices.add_argument("--max-pages", type=int, default=100)
    prices.set_defaults(func=cmd_prices)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
