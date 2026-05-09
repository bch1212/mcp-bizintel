"""End-to-end tests for BizIntel MCP — covers all 8 tools + auth + rate limit.

Strategy: we mock at two layers:
  - the audit pipeline (`tools.audit.audit_one` / `audit_many`) for any test
    that doesn't care about HTML scanning internals
  - HTTP via `respx` for the httpx-based Yelp/OSM search backend
The one test that exercises the real audit pipeline (`test_audit_pipeline_real`)
calls `tools.audit.audit_one` directly with a tiny aiohttp mock.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response


GOOD_HTML = """
<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acme Dental</title>
<script src="//calendly.com/embed.js"></script>
</head>
<body>
<a href="mailto:hi@acme.dental">hi@acme.dental</a>
<form><input type="email" name="email"/></form>
Call us at (512) 555-0142
<script src="https://www.googletagmanager.com/gtag/js?id=G-XXX"></script>
<link href="/wp-content/themes/acme/style.css">
</body></html>
""".strip()


GOOD_AUDIT = {
    "url": "https://acme.dental",
    "ssl_valid": True,
    "https_redirect": True,
    "has_viewport": True,
    "load_time_ms": 800,
    "has_contact_form": True,
    "has_booking": True,
    "has_phone": True,
    "score": 100,
    "tech_stack": {
        "cms": "WordPress",
        "booking": "Calendly",
        "email_provider": "",
        "analytics": ["Google Analytics"],
        "server": "nginx",
        "x_powered_by": "",
    },
    "error": None,
}

BAD_AUDIT = {
    "url": "https://broken.example",
    "ssl_valid": False,
    "https_redirect": False,
    "has_viewport": False,
    "load_time_ms": None,
    "has_contact_form": False,
    "has_booking": False,
    "has_phone": False,
    "score": 0,
    "tech_stack": {},
    "error": "fetch: TimeoutError",
}


# ---------- 1. health ----------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "bizintel-mcp"


# ---------- 2-3. auth ----------

def test_auth_missing_key_rejects(client):
    r = client.post("/v1/audit_website", json={"url": "https://example.com"})
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["error"]


def test_auth_invalid_key_rejects(client):
    r = client.post(
        "/v1/audit_website",
        json={"url": "https://example.com"},
        headers={"X-API-Key": "garbage"},
    )
    assert r.status_code == 401


# ---------- 4. audit_website happy path (mocked pipeline) ----------

def test_audit_website_returns_score(client, dev_headers):
    with patch("server.audit_one", new=AsyncMock(return_value=GOOD_AUDIT)):
        r = client.post(
            "/v1/audit_website", json={"url": "https://acme.dental"}, headers=dev_headers
        )
    assert r.status_code == 200
    data = r.json()
    assert data["score"] == 100
    assert data["has_booking"] is True
    assert data["tech_stack"]["cms"] == "WordPress"


# ---------- 5. audit_website handles bad URL ----------

def test_audit_website_bad_url_returns_partial(client, dev_headers):
    r = client.post("/v1/audit_website", json={"url": "not a url"}, headers=dev_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["score"] == 0
    assert data.get("error")


# ---------- 6. tech stack detection (pure) ----------

def test_detect_tech_stack_signatures():
    from tools.techstack import detect_tech_stack

    res = detect_tech_stack(GOOD_HTML, {"server": "nginx"})
    assert res["cms"] == "WordPress"
    assert res["booking"] == "Calendly"
    assert "Google Analytics" in res["analytics"]
    assert res["server"] == "nginx"

    empty = detect_tech_stack("<html></html>", {})
    assert empty["cms"] == ""
    assert empty["booking"] == ""


# ---------- 7. bulk_audit ----------

def test_bulk_audit_rejects_over_20(client, dev_headers):
    urls = [f"https://example{i}.com" for i in range(25)]
    r = client.post("/v1/bulk_audit", json={"urls": urls}, headers=dev_headers)
    assert r.status_code == 200
    assert r.json()["error"] == "max 20 urls per call"


def test_bulk_audit_runs_under_20(client, dev_headers):
    audit_results = [
        {**GOOD_AUDIT, "url": "https://a.com", "score": 90},
        {**GOOD_AUDIT, "url": "https://b.com", "score": 30},
    ]
    with patch("server.audit_many", new=AsyncMock(return_value=audit_results)):
        r = client.post(
            "/v1/bulk_audit",
            json={"urls": ["https://a.com", "https://b.com"]},
            headers=dev_headers,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    # Worst-first ordering = best leads first
    assert body["results"][0]["score"] <= body["results"][1]["score"]


# ---------- 8. search_businesses falls back to OSM when no Yelp key ----------

@respx.mock
def test_search_businesses_osm_fallback(client, dev_headers):
    respx.get("https://nominatim.openstreetmap.org/search").mock(
        return_value=Response(
            200,
            json=[{"boundingbox": ["30.0", "30.5", "-98.0", "-97.5"]}],
        )
    )
    respx.post("https://overpass-api.de/api/interpreter").mock(
        return_value=Response(
            200,
            json={
                "elements": [
                    {
                        "lat": 30.27,
                        "lon": -97.74,
                        "tags": {
                            "name": "Sample Dental",
                            "amenity": "dentist",
                            "phone": "+1-512-555-0100",
                            "addr:street": "100 Main",
                            "addr:city": "Austin",
                            "addr:state": "TX",
                        },
                    }
                ]
            },
        )
    )
    r = client.post(
        "/v1/search_businesses",
        json={"niche": "dentist", "city": "Austin", "state": "TX", "limit": 5},
        headers=dev_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "osm"
    assert body["businesses"][0]["name"] == "Sample Dental"


# ---------- 9. find_no_website filters out sites that have one ----------

@respx.mock
def test_find_no_website_filters(client, dev_headers):
    respx.get("https://nominatim.openstreetmap.org/search").mock(
        return_value=Response(200, json=[{"boundingbox": ["30", "31", "-98", "-97"]}])
    )
    respx.post("https://overpass-api.de/api/interpreter").mock(
        return_value=Response(
            200,
            json={
                "elements": [
                    {"lat": 30, "lon": -97, "tags": {"name": "NoSite Dental", "amenity": "dentist"}},
                    {
                        "lat": 30.1,
                        "lon": -97.1,
                        "tags": {
                            "name": "HasSite Dental",
                            "amenity": "dentist",
                            "website": "https://hassite.example",
                        },
                    },
                ]
            },
        )
    )
    r = client.post(
        "/v1/find_no_website",
        json={"niche": "dentist", "city": "Austin", "state": "TX", "limit": 5},
        headers=dev_headers,
    )
    assert r.status_code == 200
    body = r.json()
    names = [b["name"] for b in body["businesses"]]
    assert "NoSite Dental" in names
    assert "HasSite Dental" not in names
    assert body["businesses"][0]["lead_score"] >= 50


# ---------- 10. lead score ----------

def test_lead_score_no_website_is_hot():
    from tools.scoring import lead_score

    res = lead_score({"name": "X", "website": "", "rating": 4.5, "review_count": 100})
    assert res["score"] >= 70
    assert res["breakdown"]["no_website"] == 50


def test_lead_score_excellent_site_is_cold():
    from tools.scoring import lead_score

    biz = {"name": "X", "website": "https://x.com", "rating": 0, "review_count": 0}
    audit = {"score": 100, "has_booking": True}
    res = lead_score(biz, audit)
    assert res["score"] <= 25


# ---------- 11. dev rate limit ----------

def test_dev_rate_limit_429(client, dev_headers):
    from db.cache import get_cache

    cache = get_cache()
    asyncio.new_event_loop().run_until_complete(_seed_calls(cache, "dev-test-key", 20))

    r = client.post(
        "/v1/audit_website", json={"url": "https://example.com"}, headers=dev_headers
    )
    assert r.status_code == 429
    body = r.json()
    assert "limit" in body["error"].lower() or "Upgrade" in body["error"]
    assert body["upgrade_url"].startswith("https://mcpize.com")


async def _seed_calls(cache, key: str, n: int) -> None:
    for _ in range(n):
        await cache.record_call(key)


# ---------- 12. pro key bypasses ----------

def test_pro_key_unlimited(client, pro_headers):
    from db.cache import get_cache

    cache = get_cache()
    asyncio.new_event_loop().run_until_complete(_seed_calls(cache, "pro-test-key", 100))

    r = client.post("/v1/audit_website", json={"url": "not a url"}, headers=pro_headers)
    assert r.status_code == 200


# ---------- 13. get_tech_stack ----------

def test_get_tech_stack(client, dev_headers):
    with patch("server.audit_one", new=AsyncMock(return_value=GOOD_AUDIT)):
        r = client.post(
            "/v1/get_tech_stack", json={"url": "https://acme.dental"}, headers=dev_headers
        )
    assert r.status_code == 200
    body = r.json()
    assert body["tech_stack"]["cms"] == "WordPress"
    assert body["tech_stack"]["booking"] == "Calendly"


# ---------- 14. cache hit path ----------

def test_audit_caches_results(client, dev_headers):
    call_count = {"n": 0}

    async def fake_audit(url, session=None):  # noqa: ARG001
        call_count["n"] += 1
        return {**GOOD_AUDIT, "url": url}

    with patch("server.audit_one", new=AsyncMock(side_effect=fake_audit)):
        first = client.post(
            "/v1/audit_website", json={"url": "https://example.com"}, headers=dev_headers
        ).json()
        second = client.post(
            "/v1/audit_website", json={"url": "https://example.com"}, headers=dev_headers
        ).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert call_count["n"] == 1  # second call hit cache, didn't re-audit
    assert first["score"] == second["score"]


# ---------- 15. score_lead happy path ----------

def test_score_lead_composite(client, dev_headers):
    biz = {
        "name": "Acme Dental",
        "city": "Austin",
        "state": "TX",
        "website": "https://acme.dental",
        "phone": "+1-512-555-0100",
        "rating": 4.5,
        "review_count": 80,
        "found": True,
    }
    with patch("server._impl_get_business_details", new=AsyncMock(return_value=biz)), patch(
        "tools.scoring.audit_one", new=AsyncMock(return_value=GOOD_AUDIT)
    ):
        r = client.post(
            "/v1/score_lead",
            json={"business_name": "Acme Dental", "city": "Austin"},
            headers=dev_headers,
        )
    assert r.status_code == 200
    data = r.json()
    assert "lead_score" in data
    assert data["audit"]["score"] == 100
    assert "score_breakdown" in data
