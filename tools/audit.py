"""Async website auditor — scores sites 0-100 across SSL, mobile, speed, contact, booking.

Adapted from the patterns in leadlist/auditor.py (10-concurrent aiohttp scan).
All network errors degrade to partial results, never raise.
"""

from __future__ import annotations

import asyncio
import re
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .techstack import detect_tech_stack

DEFAULT_TIMEOUT_S = 5
DEFAULT_CONCURRENCY = 10
USER_AGENT = "BizIntelMCP/1.0 (+https://mcpize.com/bizintel-mcp)"

CONTACT_KEYWORDS = (
    "contact",
    "<form",
    'type="email"',
    "name=\"email\"",
    "mailto:",
)
BOOKING_KEYWORDS = (
    "calendly",
    "acuity",
    "booksy",
    "vagaro",
    "mindbody",
    "schedulista",
    "squareup.com/appointments",
    "book now",
    "/book",
    "schedule appointment",
    "setmore",
)
PHONE_REGEX = re.compile(
    r"(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"
)
VIEWPORT_REGEX = re.compile(
    r"<meta[^>]+name\s*=\s*['\"]viewport['\"]", re.IGNORECASE
)


def _normalize(url: str) -> str:
    """Add https:// if scheme missing."""
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _empty_result(url: str, error: str | None = None) -> dict[str, Any]:
    return {
        "url": url,
        "ssl_valid": False,
        "https_redirect": False,
        "has_viewport": False,
        "load_time_ms": None,
        "has_contact_form": False,
        "has_booking": False,
        "has_phone": False,
        "score": 0,
        "tech_stack": {},
        "error": error,
    }


async def _check_ssl(host: str) -> bool:
    """Synchronous SSL handshake offloaded to a thread."""

    def _do() -> bool:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=DEFAULT_TIMEOUT_S) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert()
                    return bool(cert)
        except Exception:
            return False

    return await asyncio.get_event_loop().run_in_executor(None, _do)


async def _check_https_redirect(session: aiohttp.ClientSession, host: str) -> bool:
    """Hit http://host and see if we land on https."""
    try:
        async with session.get(
            f"http://{host}",
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S),
        ) as resp:
            return str(resp.url).startswith("https://")
    except Exception:
        return False


def _compute_score(r: dict[str, Any]) -> int:
    score = 0
    if r["ssl_valid"]:
        score += 20
    if r["https_redirect"]:
        score += 10
    if r["has_viewport"]:
        score += 20
    if r["load_time_ms"] is not None and r["load_time_ms"] < 3000:
        score += 20
    if r["has_contact_form"]:
        score += 15
    if r["has_booking"]:
        score += 15
    return min(score, 100)


async def audit_one(
    url: str,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Audit a single URL. Always returns a full result dict, never raises."""
    norm = _normalize(url)
    if not norm:
        return _empty_result(url, error="empty url")
    parsed = urlparse(norm)
    host = parsed.hostname or ""
    if not host:
        return _empty_result(url, error="invalid url")

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S),
        )

    result = _empty_result(norm)
    try:
        ssl_task = asyncio.create_task(_check_ssl(host))
        redirect_task = asyncio.create_task(_check_https_redirect(session, host))

        body = ""
        headers: dict[str, str] = {}
        try:
            t0 = time.perf_counter()
            async with session.get(
                norm,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S),
            ) as resp:
                body = await resp.text(errors="ignore")
                headers = dict(resp.headers)
            result["load_time_ms"] = int((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            result["error"] = f"fetch: {type(exc).__name__}"

        result["ssl_valid"] = await ssl_task
        result["https_redirect"] = await redirect_task

        if body:
            lower = body.lower()
            result["has_viewport"] = bool(VIEWPORT_REGEX.search(body))
            result["has_contact_form"] = any(k in lower for k in CONTACT_KEYWORDS)
            result["has_booking"] = any(k in lower for k in BOOKING_KEYWORDS)
            result["has_phone"] = bool(PHONE_REGEX.search(body))
            result["tech_stack"] = detect_tech_stack(body, headers)

        result["score"] = _compute_score(result)
        return result
    finally:
        if own_session:
            await session.close()


async def audit_many(
    urls: list[str],
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Audit a batch of URLs concurrently. Returns results in input order."""
    if not urls:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))
    async with aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT},
        timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S),
    ) as session:

        async def bounded(u: str) -> dict[str, Any]:
            async with sem:
                return await audit_one(u, session=session)

        return await asyncio.gather(*[bounded(u) for u in urls])
