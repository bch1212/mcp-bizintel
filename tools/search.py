"""Business search — Yelp Fusion primary, OSM Overpass fallback.

Both backends return a normalized business dict so MCP tool consumers don't
have to branch on which provider answered.

Adapted from the patterns in leadlist/scraper.py.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

YELP_BASE = "https://api.yelp.com/v3"
OSM_NOMINATIM = "https://nominatim.openstreetmap.org"
OSM_OVERPASS = "https://overpass-api.de/api/interpreter"
DEFAULT_TIMEOUT_S = 10
USER_AGENT = "BizIntelMCP/1.0 (+https://mcpize.com/bizintel-mcp)"

# Loose niche → Overpass `amenity`/`shop`/`craft` mapping for the OSM fallback.
NICHE_OSM = {
    "dentist": ("amenity", "dentist"),
    "dentists": ("amenity", "dentist"),
    "doctor": ("amenity", "doctors"),
    "doctors": ("amenity", "doctors"),
    "restaurant": ("amenity", "restaurant"),
    "restaurants": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "coffee": ("amenity", "cafe"),
    "bar": ("amenity", "bar"),
    "salon": ("shop", "hairdresser"),
    "hair salon": ("shop", "hairdresser"),
    "barber": ("shop", "hairdresser"),
    "spa": ("shop", "beauty"),
    "gym": ("leisure", "fitness_centre"),
    "lawyer": ("office", "lawyer"),
    "law firm": ("office", "lawyer"),
    "real estate": ("office", "estate_agent"),
    "plumber": ("craft", "plumber"),
    "electrician": ("craft", "electrician"),
    "auto repair": ("shop", "car_repair"),
    "mechanic": ("shop", "car_repair"),
    "veterinarian": ("amenity", "veterinary"),
    "vet": ("amenity", "veterinary"),
}


def _empty_results(reason: str) -> dict[str, Any]:
    return {"businesses": [], "source": "none", "error": reason}


def _yelp_to_normal(b: dict[str, Any]) -> dict[str, Any]:
    location = b.get("location") or {}
    return {
        "name": b.get("name", ""),
        "phone": b.get("display_phone") or b.get("phone") or "",
        "website": "",  # Yelp doesn't expose website in /search; resolved via /business or absent
        "rating": b.get("rating"),
        "review_count": b.get("review_count"),
        "categories": [c.get("title") for c in (b.get("categories") or []) if c],
        "address": ", ".join(location.get("display_address") or []),
        "city": location.get("city", ""),
        "state": location.get("state", ""),
        "zip": location.get("zip_code", ""),
        "lat": (b.get("coordinates") or {}).get("latitude"),
        "lon": (b.get("coordinates") or {}).get("longitude"),
        "yelp_id": b.get("id", ""),
        "yelp_url": b.get("url", ""),
    }


def _osm_to_normal(el: dict[str, Any]) -> dict[str, Any]:
    tags = el.get("tags") or {}
    addr_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
    ]
    address = " ".join(p for p in addr_parts if p).strip()
    return {
        "name": tags.get("name", ""),
        "phone": tags.get("phone") or tags.get("contact:phone") or "",
        "website": tags.get("website") or tags.get("contact:website") or "",
        "rating": None,
        "review_count": None,
        "categories": [v for k, v in tags.items() if k in ("amenity", "shop", "craft", "office") and v],
        "address": address,
        "city": tags.get("addr:city", ""),
        "state": tags.get("addr:state", ""),
        "zip": tags.get("addr:postcode", ""),
        "lat": el.get("lat"),
        "lon": el.get("lon"),
        "yelp_id": "",
        "yelp_url": "",
    }


async def _yelp_search(
    niche: str, city: str, state: str, limit: int, api_key: str
) -> dict[str, Any]:
    location = ", ".join([p for p in (city, state) if p])
    params = {"term": niche, "location": location or "United States", "limit": min(limit, 50)}
    async with httpx.AsyncClient(
        base_url=YELP_BASE,
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT_S,
    ) as client:
        try:
            r = await client.get("/businesses/search", params=params)
        except Exception as exc:
            return _empty_results(f"yelp:{type(exc).__name__}")
        if r.status_code != 200:
            return _empty_results(f"yelp:{r.status_code}")
        payload = r.json()
        return {
            "businesses": [_yelp_to_normal(b) for b in (payload.get("businesses") or [])],
            "source": "yelp",
            "error": None,
        }


async def _yelp_details(
    business_name: str, city: str, api_key: str
) -> dict[str, Any] | None:
    """Look up a single business by name+city via Yelp /matches/best."""
    params = {
        "name": business_name,
        "city": city,
        "country": "US",
        "match_threshold": "default",
    }
    async with httpx.AsyncClient(
        base_url=YELP_BASE,
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT_S,
    ) as client:
        try:
            r = await client.get("/businesses/matches", params=params)
            if r.status_code != 200:
                return None
            matches = r.json().get("businesses") or []
            if not matches:
                return None
            biz_id = matches[0].get("id")
            if not biz_id:
                return None
            r2 = await client.get(f"/businesses/{biz_id}")
            if r2.status_code != 200:
                return _yelp_to_normal(matches[0])
            full = r2.json()
            normal = _yelp_to_normal(full)
            normal["website"] = ""  # Yelp still doesn't expose external URL; agents use yelp_url
            normal["hours"] = full.get("hours") or []
            normal["photos"] = full.get("photos") or []
            return normal
        except Exception:
            return None


async def _osm_geocode_bbox(
    city: str, state: str
) -> tuple[float, float, float, float] | None:
    location = ", ".join([p for p in (city, state) if p]) or "United States"
    params = {"q": location, "format": "json", "limit": 1}
    async with httpx.AsyncClient(
        base_url=OSM_NOMINATIM,
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT_S,
    ) as client:
        try:
            r = await client.get("/search", params=params)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data:
                return None
            bbox = data[0].get("boundingbox")
            if not bbox or len(bbox) != 4:
                return None
            # Nominatim bbox is [south, north, west, east] as strings.
            return float(bbox[0]), float(bbox[2]), float(bbox[1]), float(bbox[3])
        except Exception:
            return None


async def _osm_search(
    niche: str, city: str, state: str, limit: int
) -> dict[str, Any]:
    key = NICHE_OSM.get(niche.lower())
    if not key:
        return _empty_results(f"osm:unknown_niche:{niche}")
    bbox = await _osm_geocode_bbox(city, state)
    if not bbox:
        return _empty_results("osm:geocode_failed")
    south, west, north, east = bbox
    tag_key, tag_val = key
    overpass_q = f"""
    [out:json][timeout:25];
    (
      node["{tag_key}"="{tag_val}"]({south},{west},{north},{east});
      way["{tag_key}"="{tag_val}"]({south},{west},{north},{east});
    );
    out center {limit};
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT_S,
    ) as client:
        try:
            r = await client.post(OSM_OVERPASS, data={"data": overpass_q})
            if r.status_code != 200:
                return _empty_results(f"osm:{r.status_code}")
            elements = r.json().get("elements") or []
            return {
                "businesses": [_osm_to_normal(e) for e in elements[:limit]],
                "source": "osm",
                "error": None,
            }
        except Exception as exc:
            return _empty_results(f"osm:{type(exc).__name__}")


async def search_businesses(
    niche: str,
    city: str,
    state: str = "",
    limit: int = 20,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Primary entrypoint: Yelp if key present, else OSM fallback."""
    api_key = api_key or os.getenv("YELP_API_KEY", "")
    limit = max(1, min(int(limit or 20), 50))
    if api_key:
        return await _yelp_search(niche, city, state, limit, api_key)
    return await _osm_search(niche, city, state, limit)


async def get_business_details(
    business_name: str,
    city: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Single-business lookup. Yelp /matches first, OSM Nominatim fallback."""
    api_key = api_key or os.getenv("YELP_API_KEY", "")
    if api_key:
        match = await _yelp_details(business_name, city, api_key)
        if match:
            return match
    # OSM fallback: free-form Nominatim search.
    params = {"q": f"{business_name}, {city}", "format": "json", "limit": 1, "addressdetails": 1}
    async with httpx.AsyncClient(
        base_url=OSM_NOMINATIM,
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT_S,
    ) as client:
        try:
            r = await client.get("/search", params=params)
            if r.status_code != 200 or not r.json():
                return {"name": business_name, "city": city, "found": False}
            row = r.json()[0]
            addr = row.get("address") or {}
            return {
                "name": row.get("display_name", business_name),
                "phone": "",
                "website": "",
                "rating": None,
                "address": row.get("display_name", ""),
                "city": addr.get("city") or addr.get("town") or addr.get("village") or city,
                "state": addr.get("state", ""),
                "zip": addr.get("postcode", ""),
                "lat": float(row["lat"]) if row.get("lat") else None,
                "lon": float(row["lon"]) if row.get("lon") else None,
                "found": True,
                "source": "osm",
            }
        except Exception:
            return {"name": business_name, "city": city, "found": False}
