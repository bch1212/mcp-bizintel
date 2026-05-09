"""BizIntel MCP — paid local-business intelligence for AI agents.

Exposes both:
  * MCP-over-HTTP at /mcp (via fastmcp)
  * Plain JSON HTTP at /v1/* for direct API consumers and smoke tests

Auth: X-API-Key header. Free dev tier capped at 20 calls/24h.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

load_dotenv()

from db import keys as auth  # noqa: E402
from db.cache import get_cache  # noqa: E402
from tools.audit import audit_many, audit_one  # noqa: E402
from tools.scoring import is_hot_no_booking, lead_score, score_business  # noqa: E402
from tools.search import (  # noqa: E402
    get_business_details as _impl_get_business_details,
    search_businesses as _impl_search_businesses,
)
from tools.techstack import detect_tech_stack  # noqa: E402

# fastmcp is optional at runtime — tests don't need it, but production should.
try:
    from fastmcp import FastMCP  # type: ignore

    HAS_FASTMCP = True
except Exception:  # pragma: no cover
    FastMCP = None  # type: ignore
    HAS_FASTMCP = False


# fastmcp 2.x: build the MCP server up-front so we can take its lifespan when
# constructing FastAPI. Without this, the MCP session manager never starts and
# /mcp returns 500/404.
if HAS_FASTMCP:
    mcp = FastMCP("bizintel-mcp")
    _mcp_http_app = mcp.http_app(path="/mcp")
    _lifespan = _mcp_http_app.lifespan
else:
    mcp = None
    _mcp_http_app = None
    _lifespan = None

app = FastAPI(
    title="BizIntel MCP",
    version="1.0.0",
    description="Paid MCP server: real-time local business intelligence for AI agents.",
    lifespan=_lifespan,
)


# ---------- shared logic (used by both HTTP and MCP layers) ----------

async def _audit_website(url: str) -> dict[str, Any]:
    cache = get_cache()
    cache_key = f"audit:{url.lower().strip()}"
    hit = await cache.get(cache_key)
    if hit:
        hit["cached"] = True
        return hit
    result = await audit_one(url)
    await cache.set(cache_key, result, ttl_s=60 * 60 * 6)  # 6h
    result["cached"] = False
    return result


async def _search_businesses(niche: str, city: str, state: str = "", limit: int = 20) -> dict[str, Any]:
    cache = get_cache()
    cache_key = f"search:{niche.lower()}:{city.lower()}:{state.lower()}:{limit}"
    hit = await cache.get(cache_key)
    if hit:
        hit["cached"] = True
        return hit
    result = await _impl_search_businesses(niche, city, state, limit)
    if result.get("businesses"):
        await cache.set(cache_key, result, ttl_s=60 * 60 * 12)
    result["cached"] = False
    return result


async def _get_business_details(business_name: str, city: str) -> dict[str, Any]:
    cache = get_cache()
    cache_key = f"details:{business_name.lower()}:{city.lower()}"
    hit = await cache.get(cache_key)
    if hit:
        hit["cached"] = True
        return hit
    result = await _impl_get_business_details(business_name, city)
    await cache.set(cache_key, result, ttl_s=60 * 60 * 24)
    result["cached"] = False
    return result


async def _bulk_audit(urls: list[str]) -> dict[str, Any]:
    urls = [u for u in (urls or []) if u]
    if not urls:
        return {"results": [], "error": "no urls provided"}
    if len(urls) > 20:
        return {"results": [], "error": "max 20 urls per call"}
    results = await audit_many(urls, concurrency=10)
    results.sort(key=lambda r: r.get("score", 0))  # worst first = best leads first
    return {"results": results, "count": len(results)}


async def _score_lead(business_name: str, city: str, niche: str = "") -> dict[str, Any]:
    biz = await _get_business_details(business_name, city)
    if not biz.get("found", True) and not biz.get("name"):
        return {"business": {"name": business_name, "city": city}, "lead_score": 0, "error": "not found"}
    if niche:
        biz.setdefault("categories", []).append(niche)
    scored = await score_business(biz)
    return scored


async def _find_no_website(niche: str, city: str, state: str = "", limit: int = 20) -> dict[str, Any]:
    pool = await _search_businesses(niche, city, state, limit=max(limit * 2, limit))
    hot: list[dict[str, Any]] = []
    for b in pool.get("businesses", []):
        if not b.get("website"):
            score = lead_score(b, audit=None)
            hot.append({**b, "lead_score": score["score"], "score_breakdown": score["breakdown"]})
        if len(hot) >= limit:
            break
    hot.sort(key=lambda r: r["lead_score"], reverse=True)
    return {"businesses": hot, "count": len(hot), "source": pool.get("source", "")}


async def _find_no_booking(niche: str, city: str, state: str = "", limit: int = 20) -> dict[str, Any]:
    pool = await _search_businesses(niche, city, state, limit=max(limit * 2, limit))
    candidates = [b for b in pool.get("businesses", []) if b.get("website")]
    candidates = candidates[: max(limit * 2, limit)]
    audits = await audit_many([b["website"] for b in candidates], concurrency=10)
    hot: list[dict[str, Any]] = []
    for biz, aud in zip(candidates, audits):
        if is_hot_no_booking(biz, aud):
            score = lead_score(biz, aud)
            hot.append(
                {
                    **biz,
                    "audit_score": aud.get("score", 0),
                    "tech_stack": aud.get("tech_stack", {}),
                    "lead_score": score["score"],
                    "score_breakdown": score["breakdown"],
                }
            )
        if len(hot) >= limit:
            break
    hot.sort(key=lambda r: r["lead_score"], reverse=True)
    return {"businesses": hot, "count": len(hot), "source": pool.get("source", "")}


async def _get_tech_stack(url: str) -> dict[str, Any]:
    """Light-weight tech stack lookup — uses the audit cache so callers don't pay twice."""
    audit = await _audit_website(url)
    return {
        "url": url,
        "tech_stack": audit.get("tech_stack", {}),
        "fetched_via": "audit_cache" if audit.get("cached") else "fresh",
        "load_time_ms": audit.get("load_time_ms"),
    }


# ---------- HTTP surface ----------

async def _gate(api_key: str | None) -> JSONResponse | None:
    res = await auth.authorize(api_key)
    if not res["ok"]:
        return JSONResponse(
            status_code=res["status"],
            content={"error": res["error"], "upgrade_url": res.get("upgrade_url")},
        )
    return None


def _key(request_header: str | None) -> str:
    return (request_header or "").strip()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "bizintel-mcp",
        "version": "1.0.0",
        "yelp_configured": bool(os.getenv("YELP_API_KEY")),
        "fastmcp_loaded": HAS_FASTMCP,
    }


@app.post("/v1/audit_website")
async def http_audit_website(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    url = (payload or {}).get("url", "")
    if not url:
        return JSONResponse(status_code=400, content={"error": "url is required"})
    result = await _audit_website(url)
    await auth.record_call(_key(x_api_key))
    return result


@app.post("/v1/search_businesses")
async def http_search(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    p = payload or {}
    result = await _search_businesses(
        niche=p.get("niche", ""),
        city=p.get("city", ""),
        state=p.get("state", ""),
        limit=int(p.get("limit", 20) or 20),
    )
    await auth.record_call(_key(x_api_key))
    return result


@app.post("/v1/get_business_details")
async def http_details(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    p = payload or {}
    result = await _get_business_details(p.get("business_name", ""), p.get("city", ""))
    await auth.record_call(_key(x_api_key))
    return result


@app.post("/v1/bulk_audit")
async def http_bulk_audit(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    urls = (payload or {}).get("urls") or []
    result = await _bulk_audit(urls)
    await auth.record_call(_key(x_api_key))
    return result


@app.post("/v1/score_lead")
async def http_score_lead(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    p = payload or {}
    result = await _score_lead(p.get("business_name", ""), p.get("city", ""), p.get("niche", ""))
    await auth.record_call(_key(x_api_key))
    return result


@app.post("/v1/find_no_website")
async def http_find_no_website(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    p = payload or {}
    result = await _find_no_website(
        niche=p.get("niche", ""),
        city=p.get("city", ""),
        state=p.get("state", ""),
        limit=int(p.get("limit", 20) or 20),
    )
    await auth.record_call(_key(x_api_key))
    return result


@app.post("/v1/find_no_booking")
async def http_find_no_booking(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    p = payload or {}
    result = await _find_no_booking(
        niche=p.get("niche", ""),
        city=p.get("city", ""),
        state=p.get("state", ""),
        limit=int(p.get("limit", 20) or 20),
    )
    await auth.record_call(_key(x_api_key))
    return result


@app.post("/v1/get_tech_stack")
async def http_get_tech_stack(payload: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
    blocked = await _gate(x_api_key)
    if blocked:
        return blocked
    url = (payload or {}).get("url", "")
    if not url:
        return JSONResponse(status_code=400, content={"error": "url is required"})
    result = await _get_tech_stack(url)
    await auth.record_call(_key(x_api_key))
    return result


# ---------- MCP surface ----------

if HAS_FASTMCP and mcp is not None:

    @mcp.tool()
    async def audit_website(url: str) -> dict[str, Any]:
        """Score a website 0-100 across SSL, mobile-readiness, speed, contact, booking."""
        return await _audit_website(url)

    @mcp.tool()
    async def search_businesses(niche: str, city: str, state: str = "", limit: int = 20) -> dict[str, Any]:
        """Find local businesses by niche + city. Yelp-backed; OSM fallback if Yelp not configured."""
        return await _search_businesses(niche, city, state, limit)

    @mcp.tool()
    async def get_business_details(business_name: str, city: str) -> dict[str, Any]:
        """Resolve a single business to phone, address, website, hours, rating."""
        return await _get_business_details(business_name, city)

    @mcp.tool()
    async def bulk_audit(urls: list[str]) -> dict[str, Any]:
        """Audit up to 20 URLs concurrently. Results sorted worst-first (best leads first)."""
        return await _bulk_audit(urls)

    @mcp.tool()
    async def score_lead(business_name: str, city: str, niche: str = "") -> dict[str, Any]:
        """Composite 0-100 lead score combining audit + demand signal."""
        return await _score_lead(business_name, city, niche)

    @mcp.tool()
    async def find_no_website(niche: str, city: str, state: str = "", limit: int = 20) -> dict[str, Any]:
        """Find businesses in a niche with NO website at all — hottest cold-outreach leads."""
        return await _find_no_website(niche, city, state, limit)

    @mcp.tool()
    async def find_no_booking(niche: str, city: str, state: str = "", limit: int = 20) -> dict[str, Any]:
        """Businesses with a website but no online booking system. Big opportunity for SaaS pitches."""
        return await _find_no_booking(niche, city, state, limit)

    @mcp.tool()
    async def get_tech_stack(url: str) -> dict[str, Any]:
        """CMS, booking platform, email provider, analytics detected on a target URL."""
        return await _get_tech_stack(url)

    # Mount fastmcp's HTTP transport. The Starlette app already exposes the
    # `/mcp` path internally, so we mount it at root.
    if _mcp_http_app is not None:
        app.mount("/", _mcp_http_app)


# Re-export internal helpers for tests.
__all__ = [
    "app",
    "_audit_website",
    "_search_businesses",
    "_get_business_details",
    "_bulk_audit",
    "_score_lead",
    "_find_no_website",
    "_find_no_booking",
    "_get_tech_stack",
    "detect_tech_stack",
]


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
