# Brands Warehouse — supplier inventory pipeline

Adds supplier stock to [brandswarehouse.com](https://brandswarehouse.com), prices
it to beat the competition without losing money to the flat per-order fee, and
keeps stock and cost in step with the supplier afterwards.

Ace ([acegiftsplus.ca](https://acegiftsplus.ca/)) is the first supplier wired
up. Our own warehouse stock and partner consignment stock plug in the same way
— a supplier is a config entry, not a code change.

## The problem this solves

Ace bills **$15 flat per order**, whatever the order is. Marked up evenly that
fee is invisible on a $200 bottle and fatal on a $25 one:

| | naive 2× markup | this pipeline |
|---|---|---|
| bottle costing $4.10 | $8.20 — **loses $11 after the fee** | $37.95, 46% real margin |
| bottle costing $22.50 | $45.00 — $7 profit, then the fee eats it | $63.95, 39% real margin |
| bottle costing $64.00 | $128.00 | **$109.95** — cheaper, 32.5% real margin |

Cheap items carry the fee, so they can never be sold at a loss. Designer
bottles — where the fee is noise and the competition is real — come out
**cheaper** than the old markup, which is where the volume is.

## How a price is built

1. **Allocate the fee.** Below $60 an item carries the whole $15; the load
   tapers off and above $150 it is treated as covered. (`config/pricing.yaml`)
2. **Find the floor.** The cheapest price that still clears the target margin
   after cost, that fee share, and Shopify's 2.9% + $0.30.
3. **Aim at the market.** Where we know a competitor's price, go 5% under it —
   but never below the floor. Where the full 5% would break the floor we still
   list, just a little under them (`market_tight`). Only when they sell it for
   less than we can afford at all is the item held back, into
   `below-floor-*.csv`, rather than sold at a loss.

Margin floors step down as price rises (45% under $40, 28% over $150) because
that is where the fee actually bites.

    python -m bw.cli explain-price 52.70 --market 139.99    # the maths for one item
    python -m bw.cli order-economics                        # what the fee does to a basket

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in Shopify + Ace credentials
```

Two metafields carry the supplier link. Create them once in Shopify
(Settings → Custom data), both single line text:

| owner | namespace | key |
|---|---|---|
| Product | `supply` | `supplier` |
| Variant | `supply` | `supplier_sku` |

Without these, `sync-stock` and `reprice` cannot tell which listings are ours
to touch, and they will correctly refuse to touch anything.

## Running it

```bash
python -m bw.cli pull-catalog              # snapshot the 4,800 products we already sell
python -m bw.cli probe ace                 # check the Ace connection and field names
python -m bw.cli plan ace                  # what to add, at what price -> data/out/
python -m bw.cli apply ace                 # dry run: shows what it would create
python -m bw.cli apply ace --live          # create them, as DRAFTS
```

Then, on a schedule:

```bash
python -m bw.cli sync-stock ace --live     # stock + cost, hourly
python -m bw.cli reprice ace --live        # prices against the market, daily
```

**Every write is a dry run unless you pass `--live`.** New products are always
created as **drafts** — nothing goes on sale without you looking at it.

### What `plan` gives you

| file | what it is |
|---|---|
| `plan-ace-*.json` | the Shopify payloads `apply` will send |
| `new-ace-*.csv` | every item to be added, with cost, price, fee share and margin |
| `review-ace-*.csv` | close-but-not-certain matches — **check these**, they are the duplicate risk |
| `below-floor-ace-*.csv` | items we cannot price competitively and still make money |
| `no-image-ace-*.csv` | items that would go live without a photo |

## Ace

Ace runs its own Shopify store, so its catalog is readable as structured JSON
at `/products.json` — titles, brands, sizes, images and what is in stock right
now. No scraping, no browser pretence. That is `kind: shopify_store` in
`config/suppliers.yaml`, and it is what `plan ace` reads.

### The one thing that feed cannot tell us: our cost

`/products.json` publishes Ace's **retail** price. Our dealer cost is not in
there, and the pipeline will not invent it — with no cost configured every item
reports as unsellable, which is the right answer rather than a bug. Set
`suppliers.ace.cost.mode` to one of:

| mode | when |
|---|---|
| `price_list` | **best** — Ace sends a wholesale list; drop it at `data/ace_wholesale.csv` and it joins on SKU |
| `dealer_discount` | a flat % off their retail. Fine for planning, but check a few against a real invoice |
| `field` | if a dealer login exposes a cost field in the feed |

Their retail price is not wasted: it becomes the item's MSRP, which caps what
we ask. **We are never listed above Ace's own shelf price** — a customer could
just buy it from them. Items whose floor lands above their retail are held back
and reported, not listed. (`market.hold_above_msrp`, on by default.)

### How deep the discount has to be

The flat $15 sets a hard bar, and it is much higher on cheap goods:

```
$ python -m bw.cli discount-needed 64.99      # a $65 item on Ace's shelf
  needs about 65% off retail to work, listing at $63.95

$ python -m bw.cli discount-needed 149.99     # a $150 item
  needs about 40% off retail to work, listing at $141.95
```

At a 40% dealer discount, a $65 Ace item costs us $38.99, and $15 of order fee
puts our floor at $80 — above Ace's own price. **That item is not worth
listing at all**, and the pipeline says so rather than quietly publishing us as
the expensive option.

So the discount Ace gives us decides which half of their catalog is worth
having. Run `probe ace` once the cost mode is set and it will say how many of
their items clear the bar.

If most of Ace's catalog sits at the cheaper end, the lever is not the markup —
it is the fee. Either raise the number of items per Ace order (a minimum order
value, or bundling), or relax `fee_full_below` in `config/pricing.yaml` if
orders reliably carry more than one item. `order-economics` shows the effect.

### Getting your wholesale prices out of Ace

Three routes, best first.

**1. A price list file.** If Ace sends a price list — PDF or spreadsheet —
this is the most accurate cost there is, because it is literally what you pay,
and their lists carry live quantities too, so it doubles as a full feed:

```bash
pip install -r requirements-tools.txt
python tools/parse_pricelist.py their-list.pdf -o data/ace_wholesale.csv
python -m bw.cli probe ace_pricelist
```

The parser handles the format those lists come in — one flattened line per
item, `SKU BARCODE BRAND ITEM TYPE CONCENTRATION GENDER SIZE QTY $PRICE`, with
no column boundaries. Splitting the brand off the item name is the hard part
(brands are multi-word: "Attar Collection", "Abercrombie & Fitch"), so it
learns the brand vocabulary from the rows that repeat their brand, then applies
it to the rest. On a real 2,960-line list it parses 99.7% and finds 240 brands.
Whatever does not parse is printed rather than dropped.

**2. Your dealer login, driven from your own machine.**

```bash
pip install -r requirements-tools.txt && playwright install chromium

python tools/ace_session.py login       # a real browser opens; log in yourself
python tools/ace_session.py diagnose    # find where your wholesale prices live
python tools/ace_session.py prices      # writes data/ace_wholesale.csv
```

Your password is typed into the real Ace site in a real browser window, never
into the script, and is never stored. Only the resulting session is saved, to
`data/.ace_session.json`, which is gitignored and chmod 600.

`diagnose` is there because a dealer login can expose wholesale pricing in
several places depending on how the store is built, and guessing wrong gets you
retail prices that look perfectly plausible. It compares the site logged out
against logged in and tells you which endpoint actually carries your pricing —
or that none of them do, in which case route 1 is the answer.

**2b. Give the pipeline the session directly.** If you would rather the sync
fetch wholesale prices itself instead of you running the browser tool, set
`ACE_SESSION_COOKIE` in the environment to a dealer session cookie — copied
from your own browser, never a password — and confirm it works:

```bash
python -m bw.cli auth-check ace
```

Step-by-step, with the DevTools clicks: [docs/ace-session-cookie.md](docs/ace-session-cookie.md).

That prints each sampled product's public price against what the session sees.
Same price both ways means the session is not doing anything, and it says so
rather than letting retail prices through as if they were cost. Cookies expire,
so this needs re-pasting every so often; a price list file does not.

Running this from a hosted session additionally requires the environment's
network policy to allow `acegiftsplus.ca`, which is set per environment — see
the [Claude Code on the web docs](https://code.claude.com/docs/en/claude-code-on-the-web).

**3. A flat discount off their retail.** Quick, good enough to plan with, wrong
in detail. `cost.mode: dealer_discount` in `config/suppliers.yaml`.

### Running it before the cost question is settled

A CSV export drops straight in, with the same fee rules and pricing:

```bash
ACE_FILE=~/Downloads/ace-export.csv python -m bw.cli plan ace_file
```

## Adding our own and partner stock

`onhand` and `consignment` are already defined in `config/suppliers.yaml` —
point them at a published Google Sheet (File → Share → Publish to web → CSV)
and set the URL in `.env`. Both carry **no order fee**, so the same bottle
prices cheaper from our own shelf than dropshipped from Ace, automatically.
Give each consignment partner their own entry to keep stock and payouts apart.

## Layout

    bw/pricing.py      the fee allocation, margin floor and market keying
    bw/match.py        is this item already in our catalog?
    bw/normalize.py    "212 (M) EDT SP 1.7oz(NEW PACK)" -> brand, name, 50ml, EDT, Man
    bw/listing.py      supplier rows -> Shopify product payloads
    bw/shopify.py      Admin GraphQL client (catalog, create, price, stock, cost)
    bw/suppliers/      one adapter per kind of source (Shopify store, API, spreadsheet)
    tools/             price-list parser, and the browser login for wholesale prices
    bw/market/         competitor prices, from public /products.json, cached
    config/            pricing rules and supplier definitions — tune these, not the code

    python -m unittest discover -s tests       # 81 tests

## Notes

- Matching goes supplier SKU → barcode → SKU → brand+product+size, then fuzzy
  *within the same brand only*. Anything doubtful goes to the review file
  instead of being guessed at. Barcodes that a feed reuses across products
  (`4010000000000` and friends) are struck out rather than trusted.
- Both the Ace reader and the competitor reader use public `/products.json`,
  fetched slowly and cached, `robots.txt` checked first. Neither has been run
  against the live sites: the build sandbox's network policy blocks outbound
  hosts, including acegiftsplus.ca. They are covered by tests against recorded
  response fixtures, and will work from a normal machine or a scheduled job.
- `/products.json` carries no barcodes, so Ace items match on brand + product +
  size rather than a code. That is what the review file is for — check it on
  the first run. A parsed price list *does* carry barcodes for most rows, which
  makes it the better source for matching as well as for cost.
- Supplier price lists contain typos that split a brand in two ("Abercrombie &
  Fitch" and "Abercombie & Fitch" both appear in one real list). Matching is
  brand-scoped with a fuzzy fallback, so those still find each other, but it is
  worth a look in the review file.
- Nothing under `data/` is committed: supplier pricing, saved sessions and
  generated plans all stay local.
- Keying to the market cuts both ways: when a competitor prices *high*, we take
  the margin rather than being needlessly cheap. Raise `market.undercut` in
  `config/pricing.yaml` to be more aggressive.
