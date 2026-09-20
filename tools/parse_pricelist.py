#!/usr/bin/env python3
"""Turn a supplier's PDF price list into the CSV the pipeline reads.

Some suppliers do not have a feed -- they send a PDF price list, a few thousand
rows of:

    SKU  BARCODE  BRAND  ITEM NAME  TYPE  CONCENTRATION  GENDER  SIZE  QTY  $PRICE

which flattens to one line per item with no column boundaries. The structured
tail (type, concentration, gender, size, quantity, price) is parsed right to
left, the SKU and barcode left to right, and what remains in the middle is
"brand + item name" run together.

Splitting that middle is the hard part, because brands are multi-word ("Bond #9",
"Abercrombie & Fitch", "Aj Arabia"). Two things make it tractable: the item name
usually repeats the brand straight after it ("Armaf Armaf Momento Lace"), and
brands recur across hundreds of rows. So the first pass learns brands from the
rows that repeat, and the second pass applies that vocabulary to the rest.

Usage:
    python tools/parse_pricelist.py price_list.pdf -o data/ace_wholesale.csv
    python tools/parse_pricelist.py extracted.txt  -o data/ace_wholesale.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

GENDERS = {"man", "woman", "unisex", "men", "women", "kids", "child", "children"}

# Everything after the item name, read from the right.
TAIL = re.compile(
    r"""
    \s(?P<concentration>
        Eau(?:\s+\w+){1,3}            # Eau De Parfum, Eau Sans Alcohol
      | Elixir | Extrait | Essence | Absolu | Intense | Parfum | Perfume | Cologne
      | Body\s+Spray | Body\s+Mist | Hair\s+Mist | Deodorant | Gift\s+Set
      | Attar | Oil | Lotion | Cream | Soap | Powder | Balm | Mist | Spray | Water | Set
      )
    \s+(?P<gender>Man|Woman|Unisex|Men|Women|Kids|Child|Children)
    \s+(?P<size>[\d.,]+\s*(?:ml|g|oz|L)|N/?A)
    \s+(?P<qty>[\d,]+)
    \s*\$\s*(?P<price>[\d,]+(?:\.\d{1,2})?)
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# The barcode column is not always a barcode -- plenty of rows carry an
# internal code (ATAR9052, PTEAJ100TCAT). Anything without spaces that contains
# a digit counts; only real digit strings are kept as barcodes downstream.
HEAD = re.compile(
    r"^(?P<sku>[A-Za-z0-9][A-Za-z0-9._/#&+-]{2,})\s+"
    r"(?P<barcode>(?=[A-Za-z0-9./-]*\d)[A-Za-z0-9./-]{5,20}|NoBarcode|No\s*Barcode|N/?A)\s+"
    r"(?P<rest>.+)$"
)

PRICE_END = re.compile(r"\$\s*[\d,]+(?:\.\d{1,2})?\s*$")

NOISE = re.compile(r"\\\*\\?\*|\*{2,}|\bRARE\b|\\#", re.I)

# Packaging words the price list uses as its "Type" column.
TYPES = ("Boxed", "Unboxed", "Tester", "Gift Set", "Set", "Sample", "Miniature",
         "Damaged Box", "No Box", "Refill", "Travel")


def clean_line(line: str) -> str:
    line = NOISE.sub(" ", line)
    line = line.replace("\\", "").replace("''", '"')
    return re.sub(r"\s+", " ", line).strip()


def split_brand(blob: str, vocabulary: set[str] | None = None) -> tuple[str, str]:
    """Separate "BRAND ITEM NAME" when they are run together.

    First look for the brand repeating right after itself, which most rows do.
    Failing that, fall back to the brand vocabulary learned from the rows that
    did repeat.
    """
    tokens = blob.split()
    if not tokens:
        return "", blob

    # The brand reappears at the start of the item name, either immediately
    # ("Armaf Armaf Momento Lace") or just after a prefix word
    # ("Attar Collection Tester - Attar Collection Fleur De Santal").
    for size in range(5, 0, -1):
        head = [t.lower() for t in tokens[:size]]
        for start in range(size, min(size + 4, len(tokens) - size + 1)):
            if [t.lower() for t in tokens[start:start + size]] == head:
                return " ".join(tokens[:size]), " ".join(tokens[size:])

    if vocabulary:
        for size in range(5, 0, -1):
            candidate = " ".join(tokens[:size])
            if candidate.lower() in vocabulary:
                return candidate, " ".join(tokens[size:]) or candidate

    return tokens[0], " ".join(tokens[1:]) or tokens[0]


def strip_type(item: str) -> tuple[str, str]:
    """Pull the packaging word off the end of the item name."""
    for packaging in sorted(TYPES, key=len, reverse=True):
        pattern = rf"\s+{re.escape(packaging)}\s*$"
        if re.search(pattern, item, re.I):
            return re.sub(pattern, "", item, flags=re.I).strip(), packaging
    return item, ""


def join_wrapped(lines: list[str]) -> list[str]:
    """Rejoin rows the PDF broke across two lines.

    A row always ends in a price. One that starts like a row but has no price
    yet is holding the next line's tail.
    """
    joined: list[str] = []
    pending = ""
    for raw in lines:
        line = clean_line(raw)
        if not line:
            continue
        if pending:
            joined.append(f"{pending} {line}")
            pending = ""
            continue
        if not PRICE_END.search(line) and HEAD.match(line):
            pending = line
            continue
        joined.append(line)
    if pending:
        joined.append(pending)
    return joined


def parse_lines(lines: list[str]) -> tuple[list[dict], list[str]]:
    matched, unmatched = [], []

    for line in join_wrapped(lines):
        if line.lower().startswith("sku "):
            continue

        tail = TAIL.search(line)
        if not tail:
            unmatched.append(line)
            continue
        head = HEAD.match(line[: tail.start()].strip())
        if not head:
            unmatched.append(line)
            continue

        barcode = head.group("barcode")
        matched.append({
            "sku": head.group("sku"),
            "barcode": "" if not barcode.isdigit() else barcode,
            "blob": head.group("rest").strip(),
            "concentration": re.sub(r"\s+", " ", tail.group("concentration")).strip(),
            "gender": tail.group("gender"),
            "size": tail.group("size").replace(" ", ""),
            "qty": tail.group("qty").replace(",", ""),
            "cost": tail.group("price").replace(",", ""),
        })

    # Pass 1 taught us the brands; pass 2 applies them to the rows that did not
    # repeat their brand.
    vocabulary = Counter()
    for row in matched:
        brand, _ = split_brand(row["blob"])
        if brand:
            vocabulary[brand.lower()] += 1
    known = {brand for brand, count in vocabulary.items() if count >= 2}
    known |= {brand for brand in vocabulary if " " in brand}

    rows = []
    for row in matched:
        brand, item = split_brand(row["blob"], known)
        item, packaging = strip_type(item)
        rows.append({
            "sku": row["sku"],
            "barcode": row["barcode"],
            "brand": brand,
            "title": item,
            "type": packaging,
            "concentration": row["concentration"],
            "gender": row["gender"],
            "size": row["size"],
            "qty": row["qty"],
            "cost": row["cost"],
        })
    return rows, unmatched


def read_source(path: Path) -> list[str]:
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        import pdfplumber
    except ImportError:
        sys.exit("reading a PDF needs pdfplumber:  pip install pdfplumber")
    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").splitlines())
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="the price list (.pdf or .txt)")
    parser.add_argument("-o", "--out", type=Path, default=Path("data/wholesale.csv"))
    parser.add_argument("--show-unmatched", type=int, default=5)
    args = parser.parse_args()

    lines = read_source(args.source)
    rows, unmatched = parse_lines(lines)

    if not rows:
        sys.exit("nothing parsed -- check the file, or paste a few lines into an issue")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows) + len(unmatched)
    print(f"parsed {len(rows)} of {total} lines ({len(rows) / total:.1%}) -> {args.out}")
    print(f"  {len({r['brand'] for r in rows})} brands")
    print(f"  {sum(1 for r in rows if r['barcode'])} have a barcode")
    print(f"  {sum(int(r['qty']) for r in rows):,} units in stock")
    if unmatched and args.show_unmatched:
        print(f"\n  {len(unmatched)} lines did not parse, e.g.:")
        for line in unmatched[: args.show_unmatched]:
            print(f"    {line[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
