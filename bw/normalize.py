"""Fragrance-specific text normalization.

Supplier feeds describe the same bottle a dozen different ways -- "212 (M) EDT
SP 1.7oz(NEW PACK)", "Carolina Herrera 212 Eau de Toilette - 1.7 OZ for Men",
"212 M EDT 50ml Tester". Matching and listing both depend on boiling those down
to the same facts: brand, name, size in ml, concentration, gender, tester.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Sizes the industry actually bottles. Parsed values snap to these so 3.4oz,
# 100ml and 101ml all land on the same variant.
STANDARD_SIZES_ML = [
    3, 5, 7, 8, 10, 15, 20, 25, 30, 35, 40, 50, 60, 65, 75, 80, 90, 100, 110,
    115, 120, 125, 150, 170, 175, 200, 236, 240, 250, 300, 400, 500, 750, 800, 1000,
]

# Conversions the trade uses, which are not always the arithmetic answer
# (3.4oz is sold as 100ml, not 100.5ml).
OZ_TO_ML = {
    "0.17": 5, "0.2": 6, "0.25": 7, "0.33": 10, "0.34": 10, "0.5": 15,
    "0.67": 20, "0.68": 20, "0.75": 22, "0.8": 25, "1.0": 30, "1.7": 50,
    "1.6": 50, "2.0": 60, "2.5": 75, "2.7": 80, "3.0": 90, "3.3": 100,
    "3.4": 100, "4.0": 120, "4.2": 125, "5.0": 150, "6.7": 200, "6.8": 200,
    "8.0": 240, "8.4": 250, "10.0": 300, "17.0": 500,
}

CONCENTRATIONS = [
    (r"\bparfum\s*cologne\b", "Parfum Cologne"),
    (r"\beau\s*de\s*parfum\b|\bedp\b", "EDP"),
    (r"\beau\s*de\s*toilette\b|\bedt\b", "EDT"),
    (r"\beau\s*de\s*cologne\b|\bedc\b", "EDC"),
    (r"\beau\s*fraiche\b|\bfraiche\b", "Eau Fraiche"),
    (r"\bextrait\b|\bpure\s*parfum\b|\bparfum\s*extract\b", "Extrait"),
    (r"\bbody\s*spray\b|\bbs\b", "Body Spray"),
    (r"\bbody\s*mist\b", "Body Mist"),
    (r"\bcologne\b", "Cologne"),
    (r"\bparfum\b|\bperfume\b", "Parfum"),
]

_TESTER = re.compile(r"\btester\b|\btstr\b|\bt\s*$|\(t\)|\btst\b", re.I)
_GIFT_SET = re.compile(r"\bgift\s*set\b|\bset\b|\b\d\s*pc[s]?\b|\bcoffret\b", re.I)
_NOISE = re.compile(
    r"\(new\s*pack\)|\bnew\s*pack\b|\*+\s*rare\s*\*+|\brare\b|\bboxed\b|\bunbox(ed)?\b"
    r"|\bsp\b|\bspray\b|\bvapo(risateur)?\b|\bnatural\s*spray\b|\bnib\b|\bnew\b",
    re.I,
)


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def squash(text: str) -> str:
    """Lowercase, de-accent, collapse to single spaces, drop punctuation."""
    text = strip_accents(text or "").lower()
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- barcodes

def normalize_barcode(raw: Optional[str]) -> Optional[str]:
    """Return a 13-digit EAN, or None when the value is junk.

    Supplier sheets are full of placeholder barcodes ("4010000000000"), Excel
    scientific notation ("6.30E+12") and short internal codes. A wrong barcode
    match is worse than no match, so anything doubtful is rejected.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or "e+" in text.lower():        # Excel mangled it beyond repair
        return None
    digits = re.sub(r"\D", "", text)
    if not 11 <= len(digits) <= 14:              # EAN-8 is too collision-prone here
        return None
    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    digits = digits.zfill(13)                    # UPC-A, or a UPC that lost its zero
    if len(set(digits[3:])) <= 1:                # 401000000000x style placeholder
        return None
    return digits


# -------------------------------------------------------------------- size

def _snap(ml: float) -> Optional[int]:
    best, best_gap = None, None
    for size in STANDARD_SIZES_ML:
        gap = abs(size - ml)
        if gap <= max(5.0, size * 0.08) and (best_gap is None or gap < best_gap):
            best, best_gap = size, gap
    return best


def parse_size(text: str) -> tuple[Optional[int], str]:
    """Pull a size out of free text. Returns (millilitres, display label).

    Multi-piece sets ("3 x 10ml") report the total so a trio of 10ml rollerballs
    does not collide with a single 10ml.
    """
    if not text:
        return None, ""
    haystack = strip_accents(str(text)).lower().replace(",", ".")

    multi = re.search(r"(\d+)\s*[x*]\s*(\d+(?:\.\d+)?)\s*(ml|oz)", haystack)
    if multi:
        count = int(multi.group(1))
        each, unit = float(multi.group(2)), multi.group(3)
        each_ml = each if unit == "ml" else _oz_to_ml(each)
        if each_ml:
            total = int(round(count * each_ml))
            return total, f"{count} x {int(each_ml)}ml"

    ml = re.search(r"(\d+(?:\.\d+)?)\s*(?:ml|m\.l\.|milliliter|millilitre)\b", haystack)
    if ml:
        snapped = _snap(float(ml.group(1)))
        if snapped:
            return snapped, f"{snapped}ml"

    oz = re.search(r"(\d+(?:\.\d+)?)\s*(?:oz|fl\.?\s*oz|ounce)\b", haystack)
    if oz:
        converted = _oz_to_ml(float(oz.group(1)))
        if converted:
            return converted, f"{converted}ml"

    return None, ""


def _oz_to_ml(oz: float) -> Optional[int]:
    key = f"{oz:g}"
    if key in OZ_TO_ML:
        return OZ_TO_ML[key]
    key = f"{oz:.1f}"
    if key in OZ_TO_ML:
        return OZ_TO_ML[key]
    return _snap(oz * 29.5735)


def estimate_grams(size_ml: Optional[int]) -> int:
    """Shipping weight including bottle, cap and box.

    Fitted to the weights already used in the store's own import sheets
    (50ml -> 260g, 60ml -> 300g, 100ml -> 460g).
    """
    if not size_ml:
        return 300
    return int(round(4 * size_ml + 60))


# ------------------------------------------------------- other attributes

def parse_concentration(*texts: str) -> str:
    haystack = squash(" ".join(t for t in texts if t))
    for pattern, label in CONCENTRATIONS:
        if re.search(pattern, haystack):
            return label
    return ""


_GENDER_LETTER = {"m": "Man", "w": "Woman", "f": "Woman", "u": "Unisex"}


def parse_gender(*texts: str) -> str:
    joined = " ".join(t for t in texts if t)
    raw = strip_accents(joined).lower()

    # "(M)", "(W)" -- an explicit marker, trust it over anything else.
    paren = re.search(r"\(\s*([mwfu])\s*\)", raw)
    if paren:
        return _GENDER_LETTER[paren.group(1)]

    # "EDC M 170ml" / "M EDT" -- a bare letter riding next to the concentration.
    conc = r"(?:edt|edp|edc|parfum|perfume|cologne|extrait|bs)"
    beside = re.search(rf"\b{conc}\s+([mwfu])\b", raw) or re.search(rf"\b([mwfu])\s+{conc}\b", raw)
    if beside:
        return _GENDER_LETTER[beside.group(1)]

    # Trailing "-W" / "-M" suffixes, as used in our own SKUs.
    suffix = re.search(r"[-_]([mwu])\b\s*$", raw)
    if suffix:
        return _GENDER_LETTER[suffix.group(1)]

    haystack = squash(joined)
    if re.search(r"\bunisex\b|\bfor (both|all)\b|\bman\s*/?\s*woman\b|\bm\s*/\s*w\b", haystack):
        return "Unisex"
    if re.search(r"\bfor (her|women|woman|ladies)\b|\bwomen\b|\bwoman\b|\bfemale\b|\bladies\b|\(w\)|\bw\s*$", haystack):
        return "Woman"
    if re.search(r"\bfor (him|men|man)\b|\bmen\b|\bman\b|\bmale\b|\bhomme\b|\(m\)|\bm\s*$", haystack):
        return "Man"
    return "Unisex"


def is_tester(*texts: str) -> bool:
    return bool(_TESTER.search(" ".join(t for t in texts if t)))


def is_gift_set(*texts: str) -> bool:
    return bool(_GIFT_SET.search(" ".join(t for t in texts if t)))


# --------------------------------------------------------- titles & keys

def clean_product_name(title: str, brand: str = "") -> str:
    """Strip size, concentration, gender and packaging noise from a raw title.

    "212 (M) EDT SP 1.7oz(NEW PACK)" -> "212"
    """
    text = strip_accents(title or "")
    text = re.sub(r"\(\s*[mwu]\s*\)", " ", text, flags=re.I)
    text = re.sub(r"\b\d+\s*[x*]\s*\d+(?:\.\d+)?\s*(?:ml|oz)\b(\s*each)?", " ", text, flags=re.I)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ml|oz|fl\.?\s*oz)\b", " ", text, flags=re.I)
    text = _NOISE.sub(" ", text)
    text = _TESTER.sub(" ", text)
    for pattern, _ in CONCENTRATIONS:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(
        r"\bfor\s+(him|her|men|man|women|woman|unisex|ladies)\b|\bunisex\b|\bmen\b|\bwomen\b",
        " ", text, flags=re.I,
    )
    text = re.sub(r"\b[MWU]\b(?!\.)", " ", text)      # bare gender marker
    if brand:
        text = re.sub(rf"^\s*{re.escape(brand)}\b", " ", text, flags=re.I)
        text = _strip_by_brand(text, brand)
    text = re.sub(r"[-–—,:;|]+\s*$", " ", text)
    text = re.sub(r"^\s*[-–—,:;|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—,:;|")
    return text


# Kept as typed instead of being title-cased.
ACRONYMS = {
    "EDT", "EDP", "EDC", "VIP", "XS", "NYC", "LA", "UK", "US", "CK", "DKNY",
    "YSL", "JPG", "BS", "PC", "ML", "OZ", "II", "III", "IV", "XX", "XXL",
}


def _strip_by_brand(text: str, brand: str) -> str:
    """Drop a trailing "by <brand>" even when the feed spells the brand its own way."""
    tokens = {t for t in squash(brand).split() if len(t) > 2}
    if not tokens:
        return text

    def replace(match: re.Match) -> str:
        tail = squash(match.group(1))
        return " " if tokens & set(tail.split()) else match.group(0)

    return re.sub(r"\bby\s+([\w'&.\-]+(?:\s+[\w'&.\-]+)?)", replace, text, flags=re.I)


def title_case(text: str) -> str:
    small = {"de", "la", "le", "du", "des", "of", "the", "for", "and", "by", "et", "a"}
    words = []
    for index, word in enumerate(text.split()):
        lower = word.lower()
        if index and lower in small:
            words.append(lower)
        elif word.upper() in ACRONYMS or not word.isalpha():
            words.append(word if word.isupper() or not word.isalpha() else word.upper())
        else:
            words.append(word[:1].upper() + word[1:].lower() if word else word)
    return " ".join(words)


def listing_title(brand: str, product_name: str, gender: str = "", concentration: str = "") -> str:
    """The customer-facing product title (sizes live on the variants)."""
    parts = [title_case(brand.strip()), title_case(product_name.strip())]
    title = " ".join(p for p in parts if p).strip()
    if concentration and concentration.lower() not in title.lower():
        title = f"{title} {concentration}"
    if gender in ("Man", "Woman") and f"for {gender}".lower() not in title.lower():
        title = f"{title} for {gender}"
    return re.sub(r"\s+", " ", title).strip()


def handle(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", squash(text)).strip("-")
    return slug[:255]


def product_key(brand: str, product_name: str, concentration: str = "", gender: str = "",
                tester: bool = False) -> str:
    """Identity of a *product* (all sizes share it)."""
    return "|".join([
        squash(brand),
        squash(product_name),
        (concentration or "").lower(),
        (gender or "").lower(),
        "t" if tester else "",
    ])


def variant_key(brand: str, product_name: str, size_ml: Optional[int],
                concentration: str = "", gender: str = "", tester: bool = False) -> str:
    """Identity of a single sellable unit (what we dedupe on)."""
    return product_key(brand, product_name, concentration, gender, tester) + f"|{size_ml or 0}"
