"""The gate every product description passes before it reaches the store.

The store came over from an old site with descriptions whose headings were set
in "fancy" Unicode bold (U+1D400 and up). Somewhere in that import the
characters were stored as literal question marks, so customers saw
"???????? ?????????????: Alcohol and fragrant oils..." on a live product page,
under several hundred words of filler about "Chloe perfumes" in general.

Nothing that fails `problems()` may be written to a product. Writers call
`require_clean()`; `audit-descriptions` scans the whole store with the same
rules so the old text gets found and replaced, not just new text kept out.
"""

from __future__ import annotations

import html
import re
from typing import Optional

# Long enough for an opening, the note pyramid and the shipping line; anything
# past this is filler a shopper scrolls through on the way to "Add to cart".
MAX_WORDS = 120

_QUESTION_RUN = re.compile(r"\?{3,}")
# Mathematical bold/italic/script letters and similar "fancy font" ranges.
_FANCY = re.compile(r"[\U0001D400-\U0001D7FF\U0001F100-\U0001F1FF]")
# Text decoded with the wrong character set: "Ã©" for "é", "â€™" for "'".
_MOJIBAKE = re.compile(r"Ã.|â€|Â\s|�")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Pasted web text: links, inline styling, empty spans and award/creator trivia.
_PASTED = re.compile(r"<a\s|<span|style=|<font|class=", re.I)
# Copy written as if we made the product. We are a retailer: "our perfume",
# "our fragrance", "we created" are the manufacturer's voice, not ours.
# Case-sensitive on purpose: "My Perfumes" is a real Arabian house, and its
# name must not read as the store talking about itself.
_MAKER_VOICE = re.compile(
    r"\b([Oo]ur|my) (perfumes?|fragrances?|scents?|colognes?|formula|bottles?|products?|"
    r"concentration|blend|collection)\b|\b[Ww]e (created|designed|crafted|developed|made|formulated)\b"
    r"|\b[Aa]uthenticity [Pp]romise\b")
_BANNED_WORDS = re.compile(r"\b(clones?|dupes?|knock[- ]?offs?)\b", re.I)


def plain_text(description_html: Optional[str]) -> str:
    text = re.sub(r"<[^>]+>", " ", description_html or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def problems(description_html: Optional[str]) -> list[str]:
    """Every reason this description must not be on the store. Empty means fine."""
    raw = description_html or ""
    text = plain_text(raw)
    found = []
    if not text:
        found.append("empty")
    if _QUESTION_RUN.search(text):
        found.append("question-mark run (corrupted characters)")
    if _FANCY.search(raw):
        found.append("fancy Unicode letters (become ??? on some systems)")
    if _MOJIBAKE.search(raw):
        found.append("garbled characters (wrong text encoding)")
    if _CONTROL.search(raw):
        found.append("control characters")
    if _PASTED.search(raw):
        found.append("pasted web markup (links, spans or inline styles)")
    if _MAKER_VOICE.search(text):
        found.append("written as if we make the product (our perfume / we created)")
    if _BANNED_WORDS.search(text):
        found.append("clone/dupe wording")
    words = len(text.split())
    if words > MAX_WORDS:
        found.append(f"too long ({words} words, limit {MAX_WORDS})")
    return found


def require_clean(description_html: Optional[str], label: str = "") -> str:
    """Return the description unchanged, or refuse it."""
    found = problems(description_html)
    if found:
        where = f" for {label}" if label else ""
        raise ValueError(f"description{where} rejected: {'; '.join(found)}")
    return description_html or ""


# The house format, set by the owner from the Calvin Klein Eternity page:
# one paragraph that opens with the bold product name and describes the scent
# in our own words, then exactly these six lines in this order. Nothing after
# the list -- the shipping line used to close every description and was
# dropped because it read as boilerplate on every page.
STANDARD_LINES = ("Top notes", "Heart notes", "Base notes",
                  "Scent family", "Best for", "Concentration")
STANDARD_WORDS = (70, 110)


def standard_problems(description_html: Optional[str]) -> list[str]:
    """How a description differs from the house format. Empty means it matches."""
    raw = (description_html or "").strip()
    found = problems(raw)
    shape = re.fullmatch(r"<p><strong>[^<]+</strong>([^<]+)</p>\s*<ul>(.*)</ul>", raw, re.S)
    if not shape:
        found.append("not in the house format (bold name, one paragraph, then the list)")
        return found
    labels = re.findall(r"<li>\s*<strong>([^<]+?):</strong>\s*[^<\s][^<]*</li>", shape.group(2))
    if tuple(labels) != STANDARD_LINES:
        found.append(f"list lines are {labels or 'missing'}, expected {list(STANDARD_LINES)}")
    if len(shape.group(1).split()) < 25:
        found.append("opening paragraph too thin")
    words = len(plain_text(raw).split())
    low, high = STANDARD_WORDS
    if not low <= words <= high:
        found.append(f"{words} words, house format is {low}-{high}")
    return found
