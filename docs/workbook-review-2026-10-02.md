# Workbook review — 2 Oct 2026

Review of *BrandsWarehouse_Workbook_Improvement_Report.docx* (written by another AI)
against *BrandsWarehouse-Project-Workbook-2026-10-01.xlsx*, the data inside it, this
repo, and the live Shopify store.

The updated workbook is `BrandsWarehouse-Project-Workbook-2026-10-02.xlsx`. It is not in
git: it holds Ace's costs, and this repo keeps supplier data out on purpose
(`data/*.xlsx` in `.gitignore`).

## Bottom line

- **The plan's numbers are sound.** Every LIVE price was recomputed from scratch on all
  2,221 rows: margin, band, stock cap, tester rule, 30%-over-PO rule and retail cap. Every
  one matches. None was sampled.
- **The report is mostly right about process and wrong about nothing factual.** Its 12
  ideas were taken where they make the push safer, and left out where they add paperwork
  without catching anything (see the table below).
- **The report missed four real errors in the workbook.** It also missed one live-store
  problem. All are below and fixed where possible.

## What the review found that the report did not

| # | Finding | Evidence | Fixed? |
|---|---------|----------|--------|
| 1 | **"Never more than 95% of MSRP" is wrong.** The 95% cap is skipped whenever the margin floor is higher (`bw/pricing.py`, `if price > cap and cap >= floor`). Only prices above 100% of retail are held. | 84 of the new drafts sit at 95–100% of retail, all long tail. 22 of them are within 1% of full retail. | Text corrected on Pricing Rules. The pricing decision is yours: To Do #29. |
| 2 | **The import had 476 variants, not 475.** New Products was missing the drafted Abercrombie & Fitch Fierce (`ABEFFIEM-100B`). The sheet only showed its held-back twin. | Live store: 477 new variants = 475 + the 30 Sep test product + this one. This is also why Summary said "475 variants" and "476 rows in the CSV" in the same line. | Row added, with a comment saying the values came from the store. Counts are now 1,413 / 484 / 476. |
| 3 | **"3 packaging duplicates", not 2** (To Do #16). | Held back: Abercrombie Fierce (No Cello Wrap), Bond No 9 Forever (Fancy Cap), Kilian Straight to Heaven (No Coffret). In each case the other packaging was drafted. | Corrected and named. |
| 4 | **Summary said main items are "at least 5% under PerfumeOnline".** The engine actually requires *cheaper than* PO (it aims for 5% under). | 4 of the 24 main items with a PO price are 1–5% under it. All are at least $1 under. | Wording corrected. |
| 5 | **Rest of Canada shipping has a gap at 2.0–2.1 kg.** The Standard tiers run 0–0.75, 0.75–2 and 2.1–30 kg. A cart in the gap that is under $75 gets no Standard rate. | Read from the live store's delivery profile. | Not changed in the store. Added to Shipping and To Do #28. |

Also checked and found correct:

- **The import landed correctly.** All 462 products and 476 variants are DRAFT. Price,
  cost, stock (Ace qty, max 3), compare-at, photo and don't-oversell all match on every
  variant. 12 have no barcode, and `DIVW-100T` carries `0222233336666`, which looks
  like a placeholder (To Do #30).
- **Store counts match.** The store has 3,093 active products, as Summary says. Summary's
  2,142, the Push Plan's 86 mixed products and 5 tester titles were all re-counted and
  are right.
- **Shipping IDs and rates are correct.** Every method and condition ID on the Shipping
  sheet exists, and free shipping is still at $75 / $100.
- **Git History matches GitHub.** All 34 commits match. `1c87f12` exists only on the PC.
  The workbook dates it 30 Sep in one place and 1 Oct in two others, and that can't be
  settled from here.
- **The GitHub copy's tests pass.** 187 tests pass there. The 242 on the PC include the
  unpushed `1c87f12` work.
- **The workbook has no secrets.** It holds no credentials, only the token *type* names
  (`atkn_`, `shppa_`), which are needed to know what to revoke.

## The report's 12 recommendations

| # | Recommendation | Verdict | Why |
|---|----------------|---------|-----|
| 1 | Source snapshot + count reconciliation | **Done** | The new Checks sheet proves the counts add up, with live formulas. Its Source dates table shows when each data set goes stale. |
| 2 | Excel Tables instead of fixed ranges | **Done differently** | Formulas now use whole columns, so new rows are counted with no range editing. Tables were not added: they would fight the existing filters and the way the sheets get reloaded, and they gain nothing over whole-column formulas. A check flags any data sheet that outgrows 20,000 rows. |
| 3 | One place for pricing settings | **Done** | Pricing Rules column B is the single source. The Price Calculator, Shipping example and Checks link to it in green. A check flags any calculator cell someone types over. "2.9% + $0.30" and "60 ml" became real numbers so they can be linked. |
| 4 | Independent Audit sheet | **Done** (Checks) | 24 rule checks plus 2 info rows. A test copy with 9 planted errors tripped every intended check. Summary and Start Here show the status in one line. |
| 5 | Push Plan as an approval runbook | **Done** | It now has 10 go/no-go gates, a proposed 10-product test whose expected results are read live from the plan, and a run log. Two gates were added that the report didn't have: **re-run on fresh Ace data** (the plan is from 30 Sep) and **a fresh backup** (the 30 Sep one predates the upload). |
| 6 | To Do as a control queue | **Partly** | Added "Depends on", a live "Ready?" column, "Done / evidence" and a "Blocked" status. Renaming You/Claude to roles was skipped: it's a one-person business, and the names are clearer. |
| 7 | Shipping checklist + cart tests | **Done** | Six cart tests, plus the live-store findings. |
| 8 | Source freshness | **Done** | Part of Checks. The refresh windows are proposals in blue cells. |
| 9 | Summary as a dashboard | **Partly** | Added a status line and made "median vs PO" a real number (+14.3%). Charts were skipped: they wouldn't change a decision here. |
| 10 | Remove credential prefixes and local paths | **Not done** | The prefixes aren't secrets; they're how you know which keys to revoke. The C:\ paths are correct for the one PC this runs on. |
| 11 | Controlled vocabulary | **Done** | The allowed Action/Why/Result values live on Checks, and the checks use them. Glossary now defines product, variant and CSV row (the 462 / 476 / 483 confusion). |
| 12 | Release register | **Not done** | Git History, Files and the new run log already cover it. A fourth list would drift. |

## Decisions only you can make

- **To Do #29:** whether the 84 near-retail drafts should be published as long tail or
  held back.
- **Shipping sheet:** keep or delete the Rest of Canada free-over-$100 method.
- **Push Plan:** swap any of the 10 proposed test products before the test runs.
