"""API key + rate-limit logic.

Tiers:
  - dev (free)  : 20 calls / 24h
  - pro         : unlimited

`BIZINTEL_PRO_KEYS` env var is comma-separated. The default dev key is
configurable via `BIZINTEL_DEV_KEY` so Brett can rotate it without code
changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .cache import get_cache

DEFAULT_DEV_KEY = "bizintel-dev-key-001"
DEV_DAILY_LIMIT = 20
DAY_S = 60 * 60 * 24
UPGRADE_URL = "https://mcpize.com/bizintel-mcp"


@dataclass
class KeyInfo:
    key: str
    tier: str  # "dev" | "pro" | "invalid"
    daily_limit: int  # 0 = unlimited


def _dev_key() -> str:
    return os.getenv("BIZINTEL_DEV_KEY", DEFAULT_DEV_KEY)


def _pro_keys() -> set[str]:
    raw = os.getenv("BIZINTEL_PRO_KEYS", "") or ""
    return {k.strip() for k in raw.split(",") if k.strip()}


def classify(api_key: str | None) -> KeyInfo:
    if not api_key:
        return KeyInfo(key="", tier="invalid", daily_limit=0)
    if api_key in _pro_keys():
        return KeyInfo(key=api_key, tier="pro", daily_limit=0)
    if api_key == _dev_key():
        return KeyInfo(key=api_key, tier="dev", daily_limit=DEV_DAILY_LIMIT)
    return KeyInfo(key=api_key, tier="invalid", daily_limit=0)


async def authorize(api_key: str | None) -> dict:
    """Returns {ok: bool, tier: str, error?: str, status?: int, remaining?: int}.

    Caller is responsible for calling `record_call` after a successful op.
    """
    info = classify(api_key)
    if info.tier == "invalid":
        return {
            "ok": False,
            "tier": "invalid",
            "status": 401,
            "error": "Invalid or missing X-API-Key header",
            "upgrade_url": UPGRADE_URL,
        }
    if info.tier == "pro":
        return {"ok": True, "tier": "pro", "remaining": -1}
    # dev tier — enforce daily limit
    cache = get_cache()
    used = await cache.calls_in_window(api_key, DAY_S)
    remaining = max(0, info.daily_limit - used)
    if remaining <= 0:
        return {
            "ok": False,
            "tier": "dev",
            "status": 429,
            "error": f"Free tier limit reached ({info.daily_limit}/24h). Upgrade at {UPGRADE_URL}",
            "upgrade_url": UPGRADE_URL,
        }
    return {"ok": True, "tier": "dev", "remaining": remaining}


async def record_call(api_key: str) -> None:
    cache = get_cache()
    await cache.record_call(api_key)
