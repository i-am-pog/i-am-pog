"""Excel-backed suppliers.

Ace's cost list arrives as .xlsx with a cover sheet in front of the data, so
the sheet is named explicitly rather than guessed at, and the header row is
found rather than assumed to be row 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..models import SupplierItem
from .base import build_item, expand_env


class ExcelAdapter:
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = expand_env(config)
        self.mapping: dict[str, str] = self.config.get("fields", {})

    def _rows(self) -> list[dict[str, Any]]:
        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "reading .xlsx needs openpyxl:  pip install -r requirements.txt"
            )

        path = Path(self.config.get("path", ""))
        if not path.exists():
            raise FileNotFoundError(
                f"supplier '{self.name}': no file at {path}. "
                "Put the supplier's list there, or change `path` in config/suppliers.yaml."
            )

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheet_name = self.config.get("sheet")
            if sheet_name and sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
            else:
                # No sheet named, or the named one is gone: take the widest,
                # which is the data rather than a cover page.
                sheet = max(workbook.worksheets, key=lambda s: (s.max_column or 0))

            rows = list(sheet.iter_rows(values_only=True))
        finally:
            workbook.close()

        header_index = self._find_header(rows)
        if header_index is None:
            raise ValueError(
                f"supplier '{self.name}': could not find a header row in {path}"
            )

        headers = [str(cell).strip() if cell is not None else "" for cell in rows[header_index]]
        records = []
        for row in rows[header_index + 1:]:
            if all(cell is None or str(cell).strip() == "" for cell in row):
                continue
            records.append({
                header: row[index] if index < len(row) else None
                for index, header in enumerate(headers) if header
            })
        return records

    def _find_header(self, rows: list[tuple]) -> Optional[int]:
        """Locate the header row by looking for the columns we were told to expect."""
        explicit = self.config.get("header_row")
        if explicit:
            return int(explicit) - 1

        wanted = set()
        for source in self.mapping.values():
            wanted.update(s.lower() for s in (source if isinstance(source, list) else [source]))

        for index, row in enumerate(rows[:25]):
            labels = {str(cell).strip().lower() for cell in row if cell is not None}
            if len(labels & wanted) >= 2:
                return index
        return None

    def fetch(self) -> list[SupplierItem]:
        default_qty = int(self.config.get("assume_qty", 0) or 0)
        items = []
        for record in self._rows():
            cleaned = {(k or "").strip(): v for k, v in record.items()}
            item = build_item(self.name, cleaned, self.mapping)
            if not item:
                continue
            # A list with no stock column is a statement that everything on it
            # was available when it was published, not a live feed.
            if item.qty == 0 and default_qty and not self.mapping.get("qty"):
                item.qty = default_qty
            items.append(item)
        return items
