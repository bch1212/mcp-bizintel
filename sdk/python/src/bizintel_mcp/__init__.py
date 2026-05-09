"""BizIntel MCP — Python client.

Lightweight wrapper around the public BizIntel MCP HTTP API. For native MCP
transport, point your MCP client at https://mcp-bizintel-production.up.railway.app/mcp
with header X-API-Key.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://mcp-bizintel-production.up.railway.app"
DEFAULT_DEV_KEY = "bizintel-dev-key-001"

__version__ = "0.1.0"


class BizIntelClient:
    """Thin REST client. All methods accept the same args as the MCP tools."""

    def __init__(
        self,
        api_key: str = DEFAULT_DEV_KEY,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, headers={"X-API-Key": api_key})

    def __enter__(self) -> "BizIntelClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post(f"{self.base_url}{path}", json=payload)
        r.raise_for_status()
        return r.json()

    # --- Tools ---

    def audit_website(self, url: str) -> dict[str, Any]:
        return self._post("/v1/audit_website", {"url": url})

    def search_businesses(
        self, niche: str, city: str, state: str = "", limit: int = 20
    ) -> dict[str, Any]:
        return self._post(
            "/v1/search_businesses",
            {"niche": niche, "city": city, "state": state, "limit": limit},
        )

    def get_business_details(self, business_name: str, city: str) -> dict[str, Any]:
        return self._post(
            "/v1/get_business_details",
            {"business_name": business_name, "city": city},
        )

    def bulk_audit(self, urls: list[str]) -> dict[str, Any]:
        return self._post("/v1/bulk_audit", {"urls": urls})

    def score_lead(
        self, business_name: str, city: str, niche: str = ""
    ) -> dict[str, Any]:
        return self._post(
            "/v1/score_lead",
            {"business_name": business_name, "city": city, "niche": niche},
        )

    def find_no_website(
        self, niche: str, city: str, state: str = "", limit: int = 20
    ) -> dict[str, Any]:
        return self._post(
            "/v1/find_no_website",
            {"niche": niche, "city": city, "state": state, "limit": limit},
        )

    def find_no_booking(
        self, niche: str, city: str, state: str = "", limit: int = 20
    ) -> dict[str, Any]:
        return self._post(
            "/v1/find_no_booking",
            {"niche": niche, "city": city, "state": state, "limit": limit},
        )

    def get_tech_stack(self, url: str) -> dict[str, Any]:
        return self._post("/v1/get_tech_stack", {"url": url})

    def health(self) -> dict[str, Any]:
        r = self._client.get(f"{self.base_url}/health")
        r.raise_for_status()
        return r.json()


__all__ = ["BizIntelClient", "DEFAULT_BASE_URL", "DEFAULT_DEV_KEY", "__version__"]
