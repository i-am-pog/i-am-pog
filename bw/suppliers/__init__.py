"""Supplier registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from .base import SupplierAdapter, expand_env
from .http_api import HttpApiAdapter
from .tabular import TabularAdapter

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "suppliers.yaml"

KINDS = {
    "http": HttpApiAdapter,
    "csv": TabularAdapter,
    "sheet": TabularAdapter,
}


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text()) or {}


def get_adapter(name: str, config: Optional[dict] = None,
                path: Path = DEFAULT_CONFIG) -> SupplierAdapter:
    suppliers = (config or load_config(path)).get("suppliers", {})
    if name not in suppliers:
        raise KeyError(f"no supplier '{name}' in {path}. Known: {', '.join(sorted(suppliers))}")
    settings = suppliers[name]
    kind = settings.get("kind", "http")
    if kind not in KINDS:
        raise ValueError(f"supplier '{name}' has unknown kind {kind!r}")
    return KINDS[kind](name, settings)


def list_suppliers(path: Path = DEFAULT_CONFIG) -> list[str]:
    return sorted(load_config(path).get("suppliers", {}))
