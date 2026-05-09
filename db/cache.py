"""SQLite cache for audit + search results.

Single shared connection guarded by an asyncio.Lock — fine for a
moderate-throughput MCP. WAL mode for concurrent reads.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from typing import Any

DEFAULT_TTL_S = 60 * 60 * 24  # 24h
_LOCK = asyncio.Lock()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


class Cache:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("BIZINTEL_DB_PATH", "bizintel.db")
        self.conn = _connect(self.path)
        self._init()

    def _init(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                api_key TEXT NOT NULL,
                ts INTEGER NOT NULL
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_key_ts ON calls(api_key, ts);")

    async def get(self, key: str) -> Any | None:
        async with _LOCK:
            row = self.conn.execute(
                "SELECT v, expires_at FROM cache WHERE k = ?", (key,)
            ).fetchone()
        if not row:
            return None
        v, exp = row
        if int(exp) < int(time.time()):
            await self.delete(key)
            return None
        try:
            return json.loads(v)
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl_s: int = DEFAULT_TTL_S) -> None:
        payload = json.dumps(value, default=str)
        expires = int(time.time()) + int(ttl_s)
        async with _LOCK:
            self.conn.execute(
                "INSERT OR REPLACE INTO cache (k, v, expires_at) VALUES (?, ?, ?)",
                (key, payload, expires),
            )

    async def delete(self, key: str) -> None:
        async with _LOCK:
            self.conn.execute("DELETE FROM cache WHERE k = ?", (key,))

    async def record_call(self, api_key: str) -> None:
        async with _LOCK:
            self.conn.execute(
                "INSERT INTO calls (api_key, ts) VALUES (?, ?)",
                (api_key, int(time.time())),
            )

    async def calls_in_window(self, api_key: str, window_s: int) -> int:
        cutoff = int(time.time()) - int(window_s)
        async with _LOCK:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM calls WHERE api_key = ? AND ts >= ?",
                (api_key, cutoff),
            ).fetchone()
        return int(row[0] if row else 0)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


_cache_singleton: Cache | None = None


def get_cache() -> Cache:
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = Cache()
    return _cache_singleton


def reset_cache_for_tests(path: str) -> Cache:
    """Used by pytest to point at a tmp DB."""
    global _cache_singleton
    if _cache_singleton is not None:
        _cache_singleton.close()
    _cache_singleton = Cache(path=path)
    return _cache_singleton
