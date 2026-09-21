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

The source of truth is **`BW_Dropship_COST_INTERNAL.xlsx`** — 6,574 lines, every
one `Supplier: Ace`, each carrying what we actually pay and a market retail
reference. Put it at `data/ace_dropship_cost.xlsx` and the `ace` supplier reads
it directly.

Two columns matter, and picking the wrong one would be expensive:

| column | used as | |
|---|---|---|
| `My Cost` | **cost** | what we pay Ace |
| `My Price` | *ignored* | what we charge our own dropship customers, at 20% |
| `Retail (ref)` | **msrp** | market retail — the ceiling, and the compare-at price |

The companion `BW_Dropship_Price_List.xlsx` adds nothing: same SKUs, and its
`Price (CAD)` equals this file's `My Price` to the cent.

### What the $15 does to this catalogue

Run against the real list, the fee decides most of it:

```
cost band      items   dropship OK   stocked OK   rescued by stocking
under $25       1105           585         1083                  498
$25-60          2329          1493         2160                  667
$60-120         1795          1451         1517                   66
over $120       1345          1116         1116                    0
TOTAL           6574          4645         5876                 1231
```

**4,645 of 6,574 work as dropship. 5,876 work if we stock them.** The 1,231
difference is items that are impossible one way and healthy the other:

```
item                    cost   retail     ours   margin
Adidas 100ml            6.25       22    12.95      46%   dropship floor was $40.01
Afnan 100ml            40.46       75    68.99      38%   dropship floor was $80.43
```

A $6.25 bottle that retails at $22 has to carry the whole $15, which puts the
floor at **$40 — nearly twice retail**. Held here, it sells at $12.95 on 46%.
And the damage reaches further up than you would guess: a $40 bottle still
fails, because $15 on an item that only retails at $75 is most of the margin.

Viability by cost, on the real list:

```
    $0-9    16% listable       $60-69    80%
  $10-19    54%                $90-99    84%
  $20-29    58%                $150+     83%
  $40-49    77%
```

It never reaches 100%, because there is a second gate that has nothing to do
with the fee: **how much of retail the cost eats.** Across this list cost is a
median 44% of retail, but 66% at the ninetieth percentile, and some lines are
worse. An Al Haramain at $158 against $196 retail is 81% — there is no room for
our margin however we ship it, and it is held back either way.

Roughly, an item has to land under about **69% of retail** to clear the top
margin band with no fee, and well under that once the $15 is on it. Being
expensive does not rescue a thin line.

The two causes separate cleanly, and only one of them is fixable:

- **1,231 items are held back purely by the $15.** Stocking them fixes every one.
- **469 cost more than 69% of retail.** Held back even stocked. Nothing about
  how we ship changes those — either negotiate the cost down or leave them.

The conclusion the numbers point at: **dropship the expensive half, buy the
cheap half in by the case.** Above roughly $60 of cost the terms barely matter;
below $25 they decide everything.

```bash
python -m bw.cli compare-sources ace,ace_stocked   # the whole list, both ways
python -m bw.cli plan ace,ace_stocked              # source each item from the winner
```

### What this file does not have

Three gaps, all of which need a second source:

- **No stock.** The list says everything was in stock when it was published,
  which is not live. `assume_qty` in `config/suppliers.yaml` is a holding
  value — treat every listing as needing stock confirmed until a live feed
  exists.
- **No barcodes**, so matching against our catalogue falls back to brand +
  product + size. Read `review-*.csv` on the first run; that is where
  duplicates would come from.
- **No images.** Listings need photos. `ace_storefront` reads acegiftsplus.ca,
  which has them — that is what it is for now, not for cost.

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

## More than one list from the same supplier

The same bottle can be available on different terms -- dropshipped for $15 an
order, or bought in with no per-order fee. Cheapest sticker price does not
settle which to use: a $22 dropship item and a $26 stocked item are not what
they look like.

Sources are compared on the only number that matters — the lowest price we
could sell the item for and still clear margin, which folds in the fee, the
margin band and card costs.

```bash
python -m bw.cli compare-sources ace_dropship,ace_stocked   # head to head
python -m bw.cli plan ace_dropship,ace_stocked              # source each item from the winner
```

On Ace's real 6,574-line list, costed both ways:

```
  6504 distinct items, 1884 price out identically either way
  4620 are genuinely cheaper if we stock them

  sourcing each from its best list takes $71,049 off what we have to charge
```

The 1,884 ties are items above the fee taper, where the terms stop mattering.

## Adding our own and partner stock

`onhand` and `consignment` are already defined in `config/suppliers.yaml` —
point them at a published Google Sheet (File → Share → Publish to web → CSV)
and set the URL in `.env`. Both carry **no order fee**, so the same bottle
prices cheaper from our own shelf than dropshipped from Ace, automatically.
Give each consignment partner their own entry to keep stock and payouts apart.

## Layout

    bw/pricing.py      the fee allocation, margin floor and market keying
    bw/match.py        is this item already in our catalog?
    bw/sourcing.py     which supplier list to buy each item from
    bw/normalize.py    "212 (M) EDT SP 1.7oz(NEW PACK)" -> brand, name, 50ml, EDT, Man
    bw/listing.py      supplier rows -> Shopify product payloads
    bw/shopify.py      Admin GraphQL client (catalog, create, price, stock, cost)
    bw/suppliers/      one adapter per source kind (xlsx, csv, Shopify store, API)
    tools/             price-list parser, and the browser login for wholesale prices
    bw/market/         competitor prices, from public /products.json, cached
    config/            pricing rules and supplier definitions — tune these, not the code

    python -m unittest discover -s tests       # 95 tests

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
