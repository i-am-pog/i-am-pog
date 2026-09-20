"""Spreadsheet-backed suppliers.

Covers our own on-hand stock, a partner's consignment list, and any supplier
that mails a CSV instead of offering an API. Google Sheets work directly via
their CSV export URL.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Optional

import requests

from ..models import SupplierItem
from .base import build_item, expand_env


class TabularAdapter:
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = expand_env(config)
        self.mapping: dict[str, str] = self.config.get("fields", {})

    def _rows(self) -> list[dict[str, Any]]:
        source = self.config.get("path") or self.config.get("url")
        if not source:
            raise ValueError(f"supplier '{self.name}' needs a `path` or `url`")

        if str(source).startswith(("http://", "https://")):
            response = requests.get(source, timeout=60)
            response.raise_for_status()
            text = response.text
        else:
            text = Path(source).read_text(encoding="utf-8-sig")

        delimiter = self.config.get("delimiter", ",")
        skip = int(self.config.get("skip_rows", 0))
        if skip:
            text = "\n".join(text.splitlines()[skip:])
        return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))

    def fetch(self) -> list[SupplierItem]:
        items = []
        for row in self._rows():
            cleaned = {(k or "").strip(): v for k, v in row.items()}
            item = build_item(self.name, cleaned, self.mapping)
            if item:
                items.append(item)
        return items
