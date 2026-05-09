"""Shared pytest fixtures: tmp SQLite, test API keys, FastAPI test client."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make sibling modules importable when pytest is run from any cwd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    db = tmp_path / "bizintel-test.db"
    monkeypatch.setenv("BIZINTEL_DB_PATH", str(db))
    monkeypatch.setenv("BIZINTEL_DEV_KEY", "dev-test-key")
    monkeypatch.setenv("BIZINTEL_PRO_KEYS", "pro-test-key")
    # Force OSM fallback path for search tests by default.
    monkeypatch.delenv("YELP_API_KEY", raising=False)
    # Reset cache singleton for each test.
    from db import cache

    cache.reset_cache_for_tests(str(db))
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from server import app

    return TestClient(app)


@pytest.fixture
def dev_headers() -> dict:
    return {"X-API-Key": "dev-test-key"}


@pytest.fixture
def pro_headers() -> dict:
    return {"X-API-Key": "pro-test-key"}
