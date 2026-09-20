"""Command line entry point.

    python -m bw.cli pull-catalog          snapshot what we already sell
    python -m bw.cli probe ace             check the Ace connection and field names
    python -m bw.cli plan ace              work out what to add, and at what price
    python -m bw.cli apply ace --live      create the drafts in Shopify
    python -m bw.cli sync-stock ace --live keep stock and cost current
    python -m bw.cli reprice ace --live    re-run pricing against the market
    python -m bw.cli explain-price ...     show the maths for one item
    python -m bw.cli order-economics       what the $15 fee does to a basket

Every command that writes is a dry run unless you pass --live.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .listing import build_product_input, group_items, missing_images
from .market import ShopifyStoreMarket
from .match import CatalogIndex, partition
from .models import SupplierItem
from .pricing import PricingEngine, money
from .shopify import ShopifyClient, chunked
from .suppliers import get_adapter, list_suppliers

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "out"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_market(name: Optional[str], refresh: bool):
    if not name:
        from .market.shopify_store import empty_index
        return empty_index()
    source = ShopifyStoreMarket(name=name, domain=f"{name}.ca" if "." not in name else name)
    try:
        return source.load(refresh=refresh)
    except Exception as error:                  # network blocked, robots.txt, anything
        print(f"  ! market prices unavailable ({error}); pricing from the floor instead")
        from .market.shopify_store import empty_index
        return empty_index()


# --------------------------------------------------------------------- commands

def cmd_pull_catalog(args) -> int:
    client = ShopifyClient(dry_run=False)
    count = client.export_catalog()
    print(f"cached {count} variants from the store")
    return 0


def cmd_probe(args) -> int:
    """Show what a supplier is actually returning, before trusting it."""
    adapter = get_adapter(args.supplier)
    print(f"supplier: {args.supplier} ({type(adapter).__name__})")
    items = adapter.fetch()
    print(f"parsed {len(items)} items\n")
    for item in items[: args.limit]:
        print(f"  {item.supplier_sku:<18} {item.brand:<20} {item.title[:44]:<44} "
              f"{item.size_label or '-':>9} qty={item.qty:<6} cost={item.cost}")
    unparsed = [i for i in items if not i.sellable]
    if unparsed:
        print(f"\n  {len(unparsed)} items have no usable cost and would be skipped"
              f" -- set the `cost` block in config/suppliers.yaml")
    no_size = [i for i in items if i.size_ml is None]
    if no_size:
        print(f"  {len(no_size)} items have no recognisable size "
              f"(check the `size` field mapping)")

    priced = [i for i in items if i.sellable]
    if priced:
        engine = PricingEngine()
        quotes = [engine.quote(args.supplier, i.cost, None, i.msrp) for i in priced]
        viable = [q for q in quotes if q.sellable]
        above = [q for q in quotes if "above_supplier_retail" in q.flags]
        print(f"\n  {len(viable)} of {len(priced)} priced items are worth listing")
        if above:
            print(f"  {len(above)} would cost us more than the supplier's own shelf price"
                  f" -- the dealer discount is not deep enough for those")
    return 0


def cmd_plan(args) -> int:
    engine = PricingEngine()
    adapter = get_adapter(args.supplier)

    print(f"fetching {args.supplier}...")
    items = [i for i in adapter.fetch() if i.sellable]
    print(f"  {len(items)} sellable items")

    catalog = ShopifyClient.load_catalog()
    index = CatalogIndex(catalog)
    print(f"  {len(index)} variants already in the store")

    buckets = partition(items, index)
    print(f"  new: {len(buckets['new'])}   already carried: {len(buckets['existing'])}   "
          f"needs a look: {len(buckets['review'])}")

    market = load_market(args.market, refresh=not args.no_refresh)
    if len(market):
        print(f"  {len(market)} competitor prices loaded")

    new_items = [r.item for r in buckets["new"]]
    if args.in_stock_only:
        before = len(new_items)
        new_items = [i for i in new_items if i.in_stock]
        print(f"  dropped {before - len(new_items)} out-of-stock items")

    quotes, unsellable = {}, []
    for item in new_items:
        market_price = market.lookup(item.brand, item.title, item.size_ml) if len(market) else None
        quote = engine.quote(args.supplier, item.cost, market_price, item.msrp)
        if quote.sellable:
            quotes[item.supplier_sku] = quote
        else:
            unsellable.append((item, quote))

    groups = group_items([i for i in new_items if i.supplier_sku in quotes])
    location = args.location or ShopifyClient().location_id

    products, taken_skus = [], set()
    for group in groups.values():
        try:
            products.append(build_product_input(
                group, quotes, location_id=location, taken_skus=taken_skus,
            ))
        except ValueError:
            continue

    run = stamp()
    plan_path = OUT_DIR / f"plan-{args.supplier}-{run}.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps({
        "supplier": args.supplier,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "location_id": location,
        "products": products,
    }, indent=2))

    write_csv(OUT_DIR / f"new-{args.supplier}-{run}.csv", [
        {**item.to_dict(), **quotes[item.supplier_sku].to_dict()}
        for item in new_items if item.supplier_sku in quotes
    ])
    write_csv(OUT_DIR / f"review-{args.supplier}-{run}.csv", [
        {"supplier_sku": r.item.supplier_sku, "feed_title": r.item.title,
         "size": r.item.size_label, "score": round(r.score, 1),
         "closest_listing": r.variant.product_title if r.variant else "",
         "closest_variant": r.variant.title if r.variant else "",
         "closest_sku": r.variant.sku if r.variant else ""}
        for r in buckets["review"]
    ])
    write_csv(OUT_DIR / f"below-floor-{args.supplier}-{run}.csv", [
        {"supplier_sku": i.supplier_sku, "title": i.title, "cost": str(i.cost),
         "our_floor": str(q.floor_price), "market": str(q.market_price or ""),
         "why": "competitor sells it below what we can afford"}
        for i, q in unsellable
    ])
    no_image = missing_images([i for i in new_items if i.supplier_sku in quotes])
    write_csv(OUT_DIR / f"no-image-{args.supplier}-{run}.csv",
              [{"supplier_sku": i.supplier_sku, "title": i.title} for i in no_image])

    total = sum(q.unit_profit for q in quotes.values())
    print(f"\nplanned {len(products)} products / {len(quotes)} variants")
    print(f"  priced off the market: {sum(1 for q in quotes.values() if q.basis == 'market')}")
    print(f"  priced off the floor:  {sum(1 for q in quotes.values() if q.basis == 'floor')}")
    if unsellable:
        print(f"  {len(unsellable)} skipped -- cannot beat the market and hold margin")
    if no_image:
        print(f"  {len(no_image)} have no image (listed as drafts, chase the photos)")
    print(f"  profit if one of each sold: ${total}")
    print(f"\nplan written to {plan_path}")
    print(f"reports in {OUT_DIR}")
    return 0


def cmd_apply(args) -> int:
    plan_path = Path(args.plan) if args.plan else max(
        OUT_DIR.glob(f"plan-{args.supplier}-*.json"), default=None, key=lambda p: p.stat().st_mtime
    )
    if not plan_path or not Path(plan_path).exists():
        print("no plan found -- run `plan` first", file=sys.stderr)
        return 1

    plan = json.loads(Path(plan_path).read_text())
    products = plan["products"][: args.limit] if args.limit else plan["products"]
    client = ShopifyClient(dry_run=not args.live)

    print(f"{'creating' if args.live else 'DRY RUN -- would create'} "
          f"{len(products)} products from {Path(plan_path).name}")

    created, failed = 0, []
    for product in products:
        try:
            client.create_product(product)
            created += 1
            if args.live and created % 25 == 0:
                print(f"  {created}/{len(products)}")
        except Exception as error:
            failed.append((product.get("title"), str(error)))

    print(f"\n{'created' if args.live else 'would create'}: {created}")
    if failed:
        print(f"failed: {len(failed)}")
        for title, error in failed[:10]:
            print(f"  {title}: {error[:160]}")
    if not args.live:
        print("\nnothing was sent. re-run with --live to create them.")
    return 0 if not failed else 1


def cmd_sync_stock(args) -> int:
    """Push current supplier stock and cost onto the variants we source from them."""
    adapter = get_adapter(args.supplier)
    items = {i.supplier_sku.upper(): i for i in adapter.fetch()}
    catalog = ShopifyClient.load_catalog()
    ours = [v for v in catalog if v.supplier.lower() == args.supplier.lower()]
    print(f"{len(items)} live items from {args.supplier}, {len(ours)} variants we source from them")

    if not ours:
        print("  none tagged yet -- run `apply` first, or the supply.supplier metafield is unset")
        return 0

    client = ShopifyClient(dry_run=not args.live)
    quantities, cost_updates, delisted = [], [], 0

    for variant in ours:
        item = items.get(variant.supplier_sku.upper())
        if item is None:
            # Gone from the feed: stop selling it rather than sell what is not there.
            quantities.append((variant.inventory_item_id, 0))
            delisted += 1
            continue
        if item.qty != variant.inventory_qty:
            quantities.append((variant.inventory_item_id, max(0, item.qty)))
        if item.cost and variant.cost != item.cost:
            cost_updates.append((variant.inventory_item_id, item.cost))

    print(f"  stock changes: {len(quantities)} (of which {delisted} zeroed: gone from the feed)")
    print(f"  cost changes:  {len(cost_updates)}")

    for batch in chunked(quantities, 100):
        client.set_inventory(batch, reason="correction")
    for inventory_item_id, cost in cost_updates:
        client.update_cost(inventory_item_id, cost)

    if not args.live:
        print("\nDRY RUN -- nothing sent. re-run with --live.")
    return 0


def cmd_reprice(args) -> int:
    """Recompute prices for variants we source from a supplier."""
    engine = PricingEngine()
    adapter = get_adapter(args.supplier)
    items = {i.supplier_sku.upper(): i for i in adapter.fetch()}
    catalog = ShopifyClient.load_catalog()
    ours = [v for v in catalog if v.supplier.lower() == args.supplier.lower()]
    market = load_market(args.market, refresh=not args.no_refresh)

    client = ShopifyClient(dry_run=not args.live)
    by_product: dict[str, list[dict]] = {}
    changes, rows = 0, []

    for variant in ours:
        item = items.get(variant.supplier_sku.upper())
        if not item or not item.cost:
            continue
        market_price = market.lookup(item.brand, item.title, item.size_ml) if len(market) else None
        quote = engine.quote(args.supplier, item.cost, market_price, item.msrp)
        if not quote.sellable or quote.price == variant.price:
            continue
        update: dict = {"id": variant.variant_id, "price": str(quote.price)}
        if quote.compare_at:
            update["compareAtPrice"] = str(quote.compare_at)
        by_product.setdefault(variant.product_id, []).append(update)
        rows.append({"sku": variant.sku, "title": variant.product_title,
                     "was": str(variant.price), "now": str(quote.price),
                     "basis": quote.basis, "margin": str(quote.margin_pct)})
        changes += 1

    print(f"{changes} price changes across {len(by_product)} products")
    for product_id, variants in by_product.items():
        for batch in chunked(variants, 50):
            client.update_prices(product_id, batch)

    run = stamp()
    write_csv(OUT_DIR / f"reprice-{args.supplier}-{run}.csv", rows)
    if rows:
        print(f"detail in {OUT_DIR / f'reprice-{args.supplier}-{run}.csv'}")
    if not args.live:
        print("\nDRY RUN -- nothing sent. re-run with --live.")
    return 0


def cmd_explain_price(args) -> int:
    engine = PricingEngine()
    quote = engine.quote(args.supplier, args.cost, args.market, args.msrp)
    fee = money(engine.supplier_rules(args.supplier).get("order_fee", 0) or 0)

    print(f"  supplier cost      ${quote.cost}")
    print(f"  order fee          ${fee} flat per {args.supplier} order "
          f"-- this item carries ${quote.allocated_fee}")
    print(f"  margin required    {float(quote.min_margin) * 100:.0f}% at this price band")
    print(f"  floor price        ${quote.floor_price}   (cheapest that still clears it)")
    if quote.market_price:
        print(f"  competitor         ${quote.market_price}")
    if quote.msrp:
        print(f"  MSRP               ${quote.msrp}")
    print(f"\n  PRICE              ${quote.price}   (set by: {quote.basis})")
    if quote.compare_at:
        print(f"  compare at         ${quote.compare_at}")
    print(f"  profit per unit    ${quote.unit_profit}")
    print(f"  real margin        {float(quote.margin_pct) * 100:.1f}%")
    if quote.flags:
        print(f"  flags              {', '.join(quote.flags)}")
    return 0


def describe_session(adapter) -> list[str]:
    """Sanity-check a pasted cookie without needing the network.

    Most of the ways this goes wrong are visible before any request: nothing
    pasted, the cookie name copied without its value, or the login cookie
    missing from an otherwise real-looking cookie string.
    """
    cookie = adapter.session.headers.get("Cookie", "")
    jar = adapter.session.cookies

    if not cookie and not jar:
        return ["NONE",
                "set ACE_SESSION_COOKIE, or run tools/ace_session.py login"]

    lines = []
    if cookie:
        pairs = [c.strip() for c in cookie.split(";") if c.strip()]
        names = [p.split("=", 1)[0] for p in pairs if "=" in p]
        lines.append(f"cookie header, {len(pairs)} values, {len(cookie)} chars")
        if not names:
            lines.append("!! no name=value pairs -- copy the whole `cookie:` header")
        # Shopify sets this only once a customer is actually logged in.
        if not any("secure_customer_sig" in n for n in names):
            lines.append("!! no `secure_customer_sig` -- that is Shopify's logged-in")
            lines.append("   marker, so this was probably copied while logged out")
        else:
            lines.append("carries secure_customer_sig (logged in)")
    if jar:
        lines.append(f"{len(jar)} cookies from a saved browser session")
    return lines


def cmd_auth_check(args) -> int:
    """Is our dealer session actually giving us wholesale prices?"""
    adapter = get_adapter(args.supplier)
    if not hasattr(adapter, "price_probe"):
        print(f"{args.supplier} is not a storefront supplier -- nothing to check")
        return 1

    print(f"supplier:  {args.supplier} ({adapter.domain})")
    for line in describe_session(adapter):
        print(f"session:   {line}")
    print()

    try:
        catalog = adapter.fetch_products()[: args.sample]
    except Exception as error:
        print(f"could not reach {adapter.domain}: {error}")
        print("\nIf this is a proxy 403, the domain is not allowed by this")
        print("environment's network policy -- that has to be changed first.")
        return 1

    handles = [p["handle"] for p in catalog if p.get("handle")]
    print(f"  {'product':<40}{'public':>10}{'dealer':>10}   verdict")
    rows = adapter.price_probe(handles)
    for row in rows:
        verdict = "WHOLESALE" if row["differs"] else "same as public"
        print(f"  {row['handle'][:39]:<40}{str(row.get('public')):>10}"
              f"{str(row.get('dealer')):>10}   {verdict}")

    differing = sum(1 for r in rows if r["differs"])
    print()
    if differing == len(rows) and rows:
        print("  -> the session is working. `plan ace` will use real wholesale costs.")
    elif differing:
        print(f"  -> {differing} of {len(rows)} differ. Partial: some products may be")
        print("     priced per-customer and others not. Worth a manual look.")
    else:
        print("  -> logged in shows the SAME prices as logged out.")
        print("     Either the session expired, or this store does not put dealer")
        print("     pricing in that endpoint. Use a price list file instead:")
        print("       python tools/parse_pricelist.py list.pdf -o data/ace_wholesale.csv")
    return 0


def cmd_discount_needed(args) -> int:
    """The dealer discount an item needs before it is worth listing at all."""
    engine = PricingEngine()
    retail = money(args.retail)

    print(f"supplier shelf price ${retail} -- what we need off it to compete\n")
    print(f"  {'discount':>9}{'our cost':>10}{'floor':>9}{'we list at':>12}   outcome")

    workable = None
    for percent in range(30, 81, 5):
        discount = Decimal(percent) / 100
        cost = money(retail * (Decimal("1") - discount))
        quote = engine.quote(args.supplier, cost, None, retail)
        if quote.sellable:
            outcome = f"{float(quote.margin_pct) * 100:.0f}% margin, ${quote.unit_profit} a unit"
            workable = workable or (percent, quote)
        else:
            outcome = "above their own price -- not worth listing"
        print(f"  {percent:>8}%{str(cost):>10}{str(quote.floor_price):>9}"
              f"{(str(quote.price) if quote.sellable else '-'):>12}   {outcome}")

    if workable:
        percent, quote = workable
        print(f"\n  needs about {percent}% off retail to work, listing at ${quote.price}")
    else:
        print("\n  nothing in that range works -- at this price point the flat fee "
              "eats the item.\n  Bundle it, or set a minimum order value instead.")
    return 0


def cmd_order_economics(args) -> int:
    engine = PricingEngine()
    fee = money(engine.supplier_rules(args.supplier).get("order_fee", 0) or 0)
    print(f"{args.supplier}: ${fee} flat per order\n")
    print(f"  {'basket':<34} {'revenue':>9} {'fee':>7} {'profit':>9} {'margin':>8}")
    for label, costs in [
        ("1 cheap bottle (cost $12)", [12]),
        ("2 cheap bottles", [12, 12]),
        ("1 designer bottle (cost $53)", [52.70]),
        ("1 designer + 1 cheap", [52.70, 12]),
        ("3 designer bottles", [52.70, 52.70, 52.70]),
    ]:
        quotes = [engine.quote(args.supplier, c) for c in costs]
        order = engine.order_economics(args.supplier, quotes)
        print(f"  {label:<34} {str(order['revenue']):>9} {str(order['order_fee']):>7} "
              f"{str(order['profit']):>9} {float(order['margin_pct']) * 100:>7.1f}%")
    print(f"\n  break-even order value: ${engine.break_even_order_value(args.supplier)}")
    print("  (an order smaller than that loses money before any margin is counted)")
    return 0


# ----------------------------------------------------------------------- wiring

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bw", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def supplier_arg(sub, default_market=True):
        sub.add_argument("supplier", nargs="?", default="ace",
                         help=f"one of: {', '.join(list_suppliers())}")
        if default_market:
            sub.add_argument("--market", default="fragrancebuy",
                             help="competitor store to price against, or '' for none")
            sub.add_argument("--no-refresh", action="store_true",
                             help="use cached competitor prices even if stale")

    subparsers.add_parser("pull-catalog", help="snapshot the Shopify catalog").set_defaults(
        func=cmd_pull_catalog)

    probe = subparsers.add_parser("probe", help="check a supplier connection and its fields")
    supplier_arg(probe, default_market=False)
    probe.add_argument("--limit", type=int, default=15)
    probe.set_defaults(func=cmd_probe)

    plan = subparsers.add_parser("plan", help="decide what to add and at what price")
    supplier_arg(plan)
    plan.add_argument("--location", default="", help="Shopify location GID")
    plan.add_argument("--in-stock-only", action="store_true", default=True)
    plan.add_argument("--include-out-of-stock", dest="in_stock_only", action="store_false")
    plan.set_defaults(func=cmd_plan)

    apply_cmd = subparsers.add_parser("apply", help="create the planned products as drafts")
    supplier_arg(apply_cmd, default_market=False)
    apply_cmd.add_argument("--plan", default="", help="plan file (default: the newest)")
    apply_cmd.add_argument("--limit", type=int, default=0, help="only create the first N")
    apply_cmd.add_argument("--live", action="store_true", help="actually write to Shopify")
    apply_cmd.set_defaults(func=cmd_apply)

    sync = subparsers.add_parser("sync-stock", help="push live stock and cost into Shopify")
    supplier_arg(sync, default_market=False)
    sync.add_argument("--live", action="store_true")
    sync.set_defaults(func=cmd_sync_stock)

    reprice = subparsers.add_parser("reprice", help="recompute prices against the market")
    supplier_arg(reprice)
    reprice.add_argument("--live", action="store_true")
    reprice.set_defaults(func=cmd_reprice)

    explain = subparsers.add_parser("explain-price", help="show the maths for one item")
    explain.add_argument("cost", type=Decimal)
    explain.add_argument("--supplier", default="ace")
    explain.add_argument("--market", type=Decimal, default=None)
    explain.add_argument("--msrp", type=Decimal, default=None)
    explain.set_defaults(func=cmd_explain_price)

    auth_check = subparsers.add_parser(
        "auth-check", help="confirm a dealer session really returns wholesale prices")
    auth_check.add_argument("supplier", nargs="?", default="ace")
    auth_check.add_argument("--sample", type=int, default=6)
    auth_check.set_defaults(func=cmd_auth_check)

    discount = subparsers.add_parser(
        "discount-needed", help="what dealer discount an item needs to be worth listing")
    discount.add_argument("retail", type=Decimal, help="the supplier's own shelf price")
    discount.add_argument("--supplier", default="ace")
    discount.set_defaults(func=cmd_discount_needed)

    economics = subparsers.add_parser("order-economics", help="what the flat fee does to a basket")
    economics.add_argument("--supplier", default="ace")
    economics.set_defaults(func=cmd_order_economics)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "market", None) == "":
        args.market = None
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
