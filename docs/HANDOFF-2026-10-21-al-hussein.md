# Al Hussein list → store drafts (21 Oct 2026)

Source: `Al_Hussein_Full_Analysis_2026-10-02.xlsx` (Outlook, 2 Oct email "here is the price list compard"). 322 items.
It holds costs, so it stays out of git. Al Hussein's public catalogue (photos, barcodes) is in `data/alhussein/`.

## Result (all checked field by field against the live store: 0 differences)
- **113 products / 116 sizes created; 110 remain DRAFT**. 3 were later ARCHIVED (tag `duplicate-of-existing`) as duplicates of older pages spelled differently: Niche Emrati Mughal Forte, Miraj Exclusif, Nasmaat. Fuzzy-checked the rest; the All Over Sprays are body sprays, not the EDPs of the same name., tagged `al-hussein-oct21` and `supplier:alhussein` (5 are `supply.supplier = pca`, where PCA was cheaper).
  - 79 with house-format notes (passed `standard_problems`); 34 tagged `needs-description`.
  - 105 with a photo from Al Hussein's own site or the brand's site; 8 tagged `needs-photo`.
  - Stock 0, DENY. This is buy-in stock (full carton per item), so nothing sells until stock arrives.
  - Price = the sheet's price. Never below our margin floor, and never below Al Hussein's MAP when Al Hussein is the source.
    Compare-at = Ace retail ref where higher.
- **203 already on the store** (live, or as Ace oct13 drafts), so they were skipped: `docs/al-hussein-oct21/already_on_store.csv`.
- **3 not created**: Abaan (size unknown) and two gift sets: `not_created.csv`.

## Notes rule used
The owner loosened it on 21 Oct: two independent sources that agree are enough, even if a third conflicts. The brand's own page wins over retailers.

## Watch-outs
- 18 drafts are priced above PerfumeOnline (we can't beat PO and keep margin). See the `competition` column.
- Genesis Gemini has no description: the brand page's notes differ from the retailers'.
- Two Al Hussein photos showed the wrong gender's bottle (External Love for Women, Intense Gold Pour Femme). They were dropped and tagged `needs-photo`.
- Just Aswad 50ml: Al Hussein's barcode was 12 digits (truncated), so it was left off.
- Freight from New York is not in the cost.
