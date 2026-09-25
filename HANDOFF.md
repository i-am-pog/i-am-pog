# Brands Warehouse — project handoff

Paste this whole file into a new chat to pick the project up cold.

---

## 1. The business

**Brands Warehouse** (brandswarehouse.com) — Shopify fragrance retailer, Oakville,
Ontario, Canada. Sells designer and niche fragrance, boxed and tester, sourced from
wholesale distributors. Canadian dollars. The store is real and trading.

**Primary supplier: "Ace"** (acegiftsplus.ca). Their site sits behind Cloudflare bot
protection that blocks datacentre IPs, so their catalogue is not fetchable from a cloud
agent — see §6.

Ace's data arrives in **three different files that are not subsets of each other**, and
for most of this project only one of them was being used as a catalogue. **Read §7
before planning any expansion** — it is the largest open question in the project.

Contact details, now consistent everywhere:
2150 Winston Park Drive, Unit 203, Oakville, ON L6H 5V1 · info@brandswarehouse.com ·
+1 (289) 551-3099.

---

## 2. Hard rules — read before doing anything

- **Never ask the user to paste a token, key or password into chat.** Credentials belong
  in environment settings. The user has pasted two live credentials into an earlier
  transcript (an `atkn_…` app automation token and a `shppa_…` legacy Admin API
  password for "Perfumes ETC BW - Live"). **Neither was used, both were refused, and
  both are still un-revoked.** Keep reminding them. Do not use them if offered again.
- **Never write credentials, tokens or environment details into the repo, commits, or
  any artifact.**
- **Nothing goes on sale without the user seeing it.** Everything this pipeline creates
  is `status: DRAFT`. Publishing is the user's decision, not yours.
- **Say "% off retail", never a bare percentage.** The user asked for this explicitly
  after an ambiguous discount figure.
- **Verify against the live store, don't sample.** Several real bugs in this project
  were invisible in a 10-item spot check and obvious in a full reconciliation. Pull the
  whole set with a bulk operation and diff it against the source file.
- **Check the plan's own counts after any change to name handling.** They are the
  regression test that caught the worst near-miss in the project (§5).

---

## 3. The repo

Python 3.11 package `bw/`. Stdlib plus `requests`, `PyYAML`, `rapidfuzz`, `openpyxl`.
`Decimal` end to end for money — never floats.

Branch: `claude/brands-warehouse-inventory-vudgnl`. **187 tests, all passing.**
Run them as their own step before committing: `python3 -m unittest discover -s tests`.

| Module | Does |
|---|---|
| `bw/normalize.py` | Title/brand/size/concentration parsing. `clean_product_name`, `listing_title`, `brand_key`, `product_key`, `handle` |
| `bw/pricing.py` | Fee allocation → margin floor → market keying → competitiveness ceiling → msrp cap → charm rounding |
| `bw/match.py` | Supplier item ↔ store variant. Barcode → normalised key → fuzzy with confidence |
| `bw/listing.py` | Groups items into products, builds Shopify `ProductSetInput` |
| `bw/images.py` | Photo matching, placeholder rejection, flanker guard |
| `bw/export.py` | Plan → Shopify product-import CSV |
| `bw/suppliers/` | Supplier adapters. `shopify_store.py` can read saved `products.json` pages |
| `bw/cli.py` | All commands |

### Commands that matter

```bash
# Rebuild the plan (what to list, at what price, with which photo)
python3 -m bw.cli plan ace --catalog data/cache/bulk_catalog.jsonl \
    --images ace_storefront --no-refresh

# Turn the newest plan into a Shopify import CSV
python3 -m bw.cli export-csv --with-images-only
python3 -m bw.cli export-csv --with-images-only --limit 25   # test batch
```

`plan` prints a funnel — **new / already carried / needs a look**, photos matched,
products planned, items skipped. Those numbers are how you tell a good change from a
bad one. Memorise the current baseline:

```
6574 sellable items
3550 live variants already in the store
new: 4237   already carried: 1511   needs a look: 826
matched a photo to 2674 of 2957 items (283 still without)
planned 2771 products / 2957 variants
1280 skipped -- cannot beat the market and hold margin
```

---

## 4. Current state

**Store: 920 products in stock** (was 362). **2,504 Ace drafts** loaded and verified,
**0 published**.

The 2,504 were reconciled against the import file in full, not sampled:

| Check | Result |
|---|---|
| Product count | 2,504 — exact match to file |
| Published / active | 0 |
| SKUs in file vs store | 2,674 / 2,674, none missing either way |
| Price / cost mismatches | 0 / 0 |
| Products with no image | 0 |
| Missing price, cost, stock, weight | 0 |
| Priced at or below cost | 0 |
| Duplicate titles | 0 |

Margins 30.9%–71.5%, median 36.3%. Gross if one of each sold: **$130,982.94**.

Restock outcome: 592 variants back on sale · 48 unsourceable taken off sale ·
7 loss-making closed · 36 released after review.

Storefront corrected: Oakville (was Toronto in places) · brandswarehouse.com (all `.ca`
removed) · info@brandswarehouse.com (was four different addresses) · Concord returns
address removed · phone added · 0.0-star rating display turned off · Terms of Use and
Privacy & Safety rewritten for Ontario law, PIPEDA and CASL.

---

## 5. Bugs found and fixed — each has a test, don't reintroduce them

1. **Testers priced as boxed.** The matcher paired tester supplier rows with boxed
   listings. 104 of 592 restocked variants priced off tester costs; 7 below boxed cost
   (CK Euphoria: −$19.61/sale). Fix: `same_packaging` guard in `bw/match.py` — a tester
   never matches a boxed listing.

2. **Priced above retail.** The msrp cap was tested against the *floor*, so it was
   skipped when it fell below the floor, and a single high competitor let the price
   through. Carolina Herrera Bad Boy shipped at $236.99 against $209 retail. Fix: the
   guard runs **after** rounding, on the final price.

3. **`--market-file` silently ignored** by `reprice` and `restock` — everything priced
   off the floor, no warning. Fix: passed through at both call sites, plus an explicit
   warning when no competitor prices load.

4. **Dedupe ran only on kept rows, not flagged ones**, so the held-back list carried
   duplicates. Fix: dedupe before the keep/hold split.

5. **A correction CSV built from a stale export** would have reverted sibling variants
   to pre-restock prices. Caught before delivery; rebuilt against live store state.

6. **Placeholder images.** 140 of 630 matches were "coming soon" graphics. Fix:
   `PLACEHOLDER` regex in `bw/images.py`, rejected at library level.

7. **Flanker photo bleed.** *Jean Lowe Maitre* got *Fraiche*'s photo. ~300 of 549
   matches were the wrong bottle. Fix: `_distinctive()` strips shared fragrance
   vocabulary and requires an exact match on what's left. 549 → 251 matches.

8. **Duplicate handles.** 179 handles shared by 358 products — tester/boxed twins.
   Shopify merges by handle, so the import would have silently folded 179 products into
   their twins. Fix: testers say `(Tester)` in the title, **and** `plan_rows` now raises
   on a duplicate handle rather than writing the file.

9. **Brand repeated in title.** The brand was stripped from the front of a title only on
   a literal match, so Ace's "Abercrombie Fitch" kept its brand against our recorded
   "Abercrombie & Fitch". Fix: `brand_key()` compares on letters and digits with a
   spelled-out "and" dropped.

10. **The fix that was worse than the bug — read this one.** 17 listings read "Bob
    Mackie Bob Mackie EDT for Man" (signature scents named after the house). The obvious
    fix — let the brand strip consume the whole name — passed every test and was wrong:
    **131 items stopped matching stock already on the store** and would have shipped as
    duplicate listings. Cause: size and concentration are stripped *before* that code
    runs, so for some rows the only words left *are* the brand, and emptying those
    collapses unrelated products onto one `product_key`. Fix: de-duplicate the brand in
    `listing_title`, which is downstream of `product_key` and cannot affect matching.
    **The plan's counts caught this, not the test suite.**

11. **Shopify import CSV header.** Shopify validates the header against its own template
    and rejects the file for missing `Option2 Value` / `Option3 Value`, even when nothing
    uses a second option. They're present and blank.

12. **`--limit` overwrote the full export**, so the last file on disk claimed to be
    everything while holding 25 of 2,576 products. Fix: limited runs get `-firstN` in the
    filename.

---

## 6. Environment and access

**Shopify** — via MCP GraphQL (`mcp__Shopify__graphql_query` / `graphql_mutation`).
Works: `productVariantsBulkUpdate`, `productUpdate`, `pageUpdate` (body is `String`, not
`HTML!`), `articleUpdate` (body **is** `HTML!`), `productsCount`, `productByHandle`,
`bulkOperationRunQuery`, `themes.files` (read).

**Blocked, don't waste turns retrying:**
- `shopPolicyUpdate` — needs `write_legal_policies`, not granted. Policy edits are
  manual, in Settings → Policies.
- `themeFilesUpsert` on the live theme.
- Ace's site (acegiftsplus.ca) — Cloudflare blocks datacentre IPs. One browser-UA
  attempt was made and abandoned. **The catalogue comes from `products.json` pages the
  user saves from their own browser**, configured as `files: data/ace_storefront/page-*.json`
  in `config/suppliers.yaml`. 47 pages → 11,600 products → 20,110 variants. See §7 —
  this is not just an image source, and that was missed for most of the project.
- `cdn.shopify.com` is reachable, which is why image URLs work.

**Bulk operations** are the right tool for verifying the whole catalogue.
`bulkOperationRunQuery` → poll `currentBulkOperation` → `curl` the URL → JSONL where a
line with `__parentId` is a variant of the product line above it.

**Shopify product-import CSV shape** — easy to get wrong:
- A multi-variant product is **several rows**; only the **first** carries product-level
  columns (title, body, vendor, tags, SEO). Repeating them makes each row its own
  product and the last wins.
- Extra images are their own rows: handle + `Image Src` + `Image Position`, nothing else.
- **Shopify merges rows by `Handle`.** A duplicate handle doesn't fail the import — it
  silently folds products together.
- Set "Overwrite existing products" knowing it matches on handle. Before any import,
  check planned handles against live store handles. (Last check: 0 collisions.)

---

## 7. The supplier-data problem — OPEN, and the biggest thing here

**Read this before planning any catalogue expansion.** It was found late and it changes
what the project can do next.

### There are three Ace sources, not one

| Source | Variants | What it actually is |
|---|---|---|
| `data/ace_dropship_cost.xlsx` | 6,574 | The only source used for everything so far |
| `data/ace_storefront/page-*.json` | 20,103 | Ace's B2B portal. **Used only for photos** |
| `data/ace_wholesale.csv` | 2,952 | A second list. **Never used at all until now** |

**The storefront is not a shop — it's Ace's dropship portal, and its prices ARE your
costs.** Verified: on the 6,571 variants shared with the dropship spreadsheet, the
storefront price equals `My Cost` on **6,532 of them (99%)**. It was treated as an image
source for the whole project.

The consequence: **the dropship spreadsheet is a subset of Ace's real dropship
catalogue.** 13,532 storefront variants aren't in it. Most are out of stock, but
**512 are in stock** and were never considered for listing.

Worked example — the user asked why Afnan 9PM Night Out wasn't on the site. It's on the
storefront at **$46.99, in stock**, and on the wholesale list at **$49.99**. It is absent
from the dropship spreadsheet entirely. Nothing to do with buying terms; the spreadsheet
is just smaller than the catalogue.

### The blocker: no retail reference outside the spreadsheet

`Retail (ref)` in the dropship xlsx is a genuine column (559 distinct values over 6,574
rows) and it is doing **nearly all** the ceiling work in the pricing engine — competitor
matching is weak (only 8 of 2,957 dropship items priced off the market).

**Neither the storefront nor the wholesale list has a retail column.** Run against the
wholesale list, **1,628 of 1,629 priced items came out with no ceiling at all** — no msrp,
no competitor price. Every price was margin floor + charm rounding, unbounded above.
That is exactly the condition that produced the $236.99-against-$209 listing in §5.2,
except there the retail reference existed and caught it.

Proof it's not theoretical: 4711 Echt Kölnisch Wasser 60ml priced out at **$42.95**. It's
a drugstore cologne.

**So: nothing from the wholesale list or the storefront-only stock can be priced safely
until a retail reference exists for it.** The pipeline will happily generate numbers.
They are not trustworthy.

### What was built anyway

`ace_wholesale` and `ace_wholesale_stocked` are wired up in `config/suppliers.yaml` and
`config/pricing.yaml`. The user confirms Ace sells single pieces off the wholesale list,
so the same $15-per-order terms apply; the `_stocked` variant zeroes the fee for the
"what's worth holding?" comparison.

Results, for reference — **do not act on these prices**:

| | Dropship terms | Stocked |
|---|---|---|
| Products / variants | 1,527 / 1,629 | 1,528 / 1,630 |
| Skipped | 53 | 52 |
| Gross if one of each sold | $77,568.96 | $76,748.68 |

`compare-sources ace_wholesale,ace_wholesale_stocked`: stocking wins on 1,972 items,
never loses, and would take **$5,950.69** off what has to be charged across the list.
That's an argument for stocking fast movers, not for changing terms now.

### Other findings from the same dig

- **638 of the 640** items on both the wholesale and dropship lists are **cheaper on
  dropship**. Where Ace carries a bottle both ways, wholesale is almost always worse.
- The wholesale list's real value is **887 items found in no other Ace source**, plus two
  things the dropship file lacks: **real stock quantities** (vs the `assume_qty: 3`
  holding value) and **barcodes** (which would let matching use the reliable key instead
  of brand + product + size).
- 579 of the 1,629 wholesale items have no photo.

### Next steps proposed, not yet started

1. **Ask Ace for a retail/MSRP column on the wholesale list.** This unblocks both the
   wholesale list and the storefront-only stock at once. Highest-value single action.
2. Export the **887 wholesale-only items** as a list the user can send Ace to price.
3. Check whether **9PM Night Out and the other 512 in-stock storefront-only items** can
   be sourced through the storefront feed as a dropship supplier, since the prices are
   already confirmed to be dropship costs.
4. Consider using wholesale **barcodes** to improve catalogue matching generally.

---

## 8. Open work

### Only the user can do these
- **Revoke the two pasted credentials.** Still outstanding. Regenerating the `shppa_`
  one breaks whatever integration uses that app — they should check that first.
- **Four policy emails** (Settings → Policies), all to `info@brandswarehouse.com`:
  Contact (mailto link goes to `.ca` while the text says `.com`; phone field also empty),
  Refund (3 addresses), Shipping (2), Terms of service (1, final section). Privacy policy
  is already correct.
- **Settings → Store details** phone field is blank; should be 289-551-3099.

### Decisions pending
- **The supplier-data problem in §7** — biggest open item. Nothing from the wholesale
  list or the storefront-only stock can be published until a retail reference exists.
- **Publishing strategy for 2,504 drafts.** Top 20 brands are 27% of items but **40% of
  gross** — proposed as a first wave. User hasn't decided.
- **16 ambiguous titles** where the supplier appends the brand to the name. 11 should be
  stripped (Al Haramain "Dazzle Intense Al Haramain", Azzaro "Solarissimo Levanzo
  Azzaro", Caron "Muguet du Bonheur Caron", Juliette Has a Gun "In the Mood for Oud…",
  Lalique "L'Amour Lalique", Moschino "Uomo Moschino" ×2, Mugler "Take Me Out Mugler"
  ×2, Nasomatto "Nudiflorum Nasomatto" ×2). 5 are real product names and must stay
  (Caron "Lady Caron", FOMO "The Envy of FOMO", Hermès "Tutti Twilly d'Hermès", Nabeel
  "Acqua di Nabeel", Stetson "Lady Stetson"). The user was asked to confirm; **the 11
  strips are approved-pending and not yet applied.** Apply in place via `productUpdate`
  so the products keep their records and photos, as was done for the 17 signature scents.
- **849 items flagged "needs a look":** 337 testers, 329 new sizes of products already
  carried (should become *variants on existing listings*, not standalone products),
  183 genuinely new.
- **267 priced, sellable products held back for want of a photo.**
- **Reviews:** no review history at all, which is why the 0.0-star display had to be
  switched off. Judge.me review-request emails plus a past-order import would start
  building one. Recommended, not started.

### Known cosmetic issues, not fixed
- Some tester listings use a photo of a different size (e.g. Acqua di Parma Cipresso:
  150ml listing, 30ml tester photo). The image matcher checks the scent, not the size.
- `FOMO` is recorded as the vendor where the feed says "FOMO Parfums", so "Parfums"
  leaks into the product name. Setting the vendor to "FOMO Parfums" fixes it. One product.
- Titles like "24 Scentstory 24 Elixir Platinum" repeat "24", but there it's genuinely
  the product line, so stripping it would be wrong.

---

## 9. Working style the user expects

They move fast, test in the live admin, and say "check it" — meaning *verify against the
store, properly*. They have caught real problems by looking at the storefront. When they
say something is done, confirm it independently rather than taking it on trust; that has
surfaced defects more than once.

Be direct about bad news and quantify it. When a fix turns out to be wrong, say so
plainly and give the number that proves it.
