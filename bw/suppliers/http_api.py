"""HTTP-backed suppliers -- Ace's dealer portal being the first.

The shape of Ace's API is described in config/suppliers.yaml rather than in
code: which path lists the catalog, how it paginates, where in the JSON the
items live and what each field is called. Pointing this at the real portal is
a config edit and a credential, not a rewrite.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

from ..models import SupplierItem
from .base import build_item, dig, expand_env


class HttpApiAdapter:
    def __init__(self, name: str, config: dict[str, Any],
                 session: Optional[requests.Session] = None):
        self.name = name
        self.config = expand_env(config)
        self.mapping: dict[str, str] = self.config.get("fields", {})
        self.session = session or requests.Session()
        self.base_url = (self.config.get("base_url") or "").rstrip("/")

    # ----------------------------------------------------------------- auth

    def authenticate(self) -> None:
        auth = self.config.get("auth", {}) or {}
        mode = (auth.get("mode") or "none").lower()

        if mode == "none":
            return
        if mode == "bearer":
            token = auth.get("token")
            if not token:
                raise ValueError(f"{self.name}: auth mode 'bearer' needs a token "
                                 f"(set the env var named in config/suppliers.yaml)")
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif mode == "header":
            name = auth.get("header_name", "X-API-Key")
            self.session.headers[name] = auth.get("token", "")
        elif mode == "basic":
            self.session.auth = (auth.get("username", ""), auth.get("password", ""))
        elif mode == "session":
            # Log in once, then carry whatever the portal hands back.
            response = self.session.post(
                self.base_url + auth.get("login_path", "/login"),
                json={
                    auth.get("username_field", "username"): auth.get("username", ""),
                    auth.get("password_field", "password"): auth.get("password", ""),
                },
                timeout=60,
            )
            response.raise_for_status()
            token_path = auth.get("token_path")
            if token_path:
                token = dig(response.json(), token_path)
                if not token:
                    raise ValueError(f"{self.name}: no token at '{token_path}' in the login response")
                self.session.headers["Authorization"] = f"Bearer {token}"
            # Without a token_path we rely on the session cookie the login set.
        else:
            raise ValueError(f"{self.name}: unknown auth mode {mode!r}")

    # -------------------------------------------------------------- fetching

    def _pages(self) -> list[Any]:
        catalog = self.config.get("catalog", {}) or {}
        path = catalog.get("path", "/")
        method = (catalog.get("method") or "GET").upper()
        items_path = catalog.get("items_path", "")
        page_param = catalog.get("page_param")
        cursor_param = catalog.get("cursor_param")
        next_path = catalog.get("next_path")
        size_param = catalog.get("page_size_param")
        page_size = catalog.get("page_size", 250)
        max_pages = int(catalog.get("max_pages", 500))
        delay = float(catalog.get("delay_seconds", 0.2))

        params: dict[str, Any] = dict(catalog.get("params") or {})
        if size_param:
            params[size_param] = page_size

        collected: list[Any] = []
        page, cursor = 1, None

        for _ in range(max_pages):
            call_params = dict(params)
            if page_param:
                call_params[page_param] = page
            if cursor_param and cursor:
                call_params[cursor_param] = cursor

            response = self.session.request(
                method, self.base_url + path, params=call_params, timeout=90
            )
            response.raise_for_status()
            payload = response.json()
            batch = dig(payload, items_path, []) or []
            if not isinstance(batch, list):
                raise ValueError(f"{self.name}: '{items_path}' is not a list of items")
            collected.extend(batch)

            if next_path:
                cursor = dig(payload, next_path)
                if not cursor:
                    break
            elif page_param:
                if len(batch) < int(page_size):
                    break
                page += 1
            else:
                break
            time.sleep(delay)

        return collected

    def fetch(self) -> list[SupplierItem]:
        self.authenticate()
        items = []
        for raw in self._pages():
            if isinstance(raw, dict):
                item = build_item(self.name, raw, self.mapping)
                if item:
                    items.append(item)
        return items
