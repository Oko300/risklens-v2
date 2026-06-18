"""
core/cache.py — RiskLens v2
=============================
Upstash Redis caching layer (replaces the prior SQLite implementation).

WHY UPSTASH: Render's free-tier filesystem is ephemeral — the SQLite
.db file reset on every redeploy, which silently erased all cached
results and made caching look broken even when the code was correct.
Upstash's free tier gives a persistent, hosted Redis instance reachable
over REST HTTP (no TCP connection pooling needed), so the cache now
survives redeploys and restarts.

Setup (one-time):
  1. Create a free database at https://console.upstash.com
  2. Copy the REST URL and REST TOKEN from the database details page
  3. Set these env vars on Render:
       UPSTASH_REDIS_REST_URL
       UPSTASH_REDIS_REST_TOKEN
  4. If unset, this module degrades gracefully to "no caching" rather
     than crashing — every tool still works, just without the speedup.

Same public API as before (cache_get, cache_set, make_cache_key,
cache_stats) so no tool code needs to change beyond the import source.
"""

import json
import os
import time
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_TTL_DAYS = max(3, min(int(os.getenv("CACHE_TTL_DAYS", "7")), 7))
CACHE_TTL_SECS = CACHE_TTL_DAYS * 86_400

UPSTASH_URL   = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

_CACHE_ENABLED = bool(UPSTASH_URL and UPSTASH_TOKEN)

_client: Optional[httpx.AsyncClient] = None
_sync_client: Optional[httpx.Client] = None

if _CACHE_ENABLED:
    print(f"[cache:init] Upstash Redis caching ENABLED (TTL={CACHE_TTL_DAYS}d) at {UPSTASH_URL}")
else:
    print(
        "[cache:init] Upstash Redis NOT configured (UPSTASH_REDIS_REST_URL / "
        "UPSTASH_REDIS_REST_TOKEN missing) — caching disabled, tools will run uncached."
    )


def _get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None or _sync_client.is_closed:
        _sync_client = httpx.Client(
            timeout=httpx.Timeout(8.0),
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        )
    return _sync_client


# ---------------------------------------------------------------------------
# Public API — synchronous, matching prior cache.py call sites
# (tool code calls these without await, so we keep that contract and use
# a short-timeout sync httpx.Client under the hood)
# ---------------------------------------------------------------------------

def cache_get(cache_key: str) -> Optional[dict]:
    """Return cached result dict if valid, else None. Never raises."""
    if not _CACHE_ENABLED:
        return None
    try:
        client = _get_sync_client()
        resp = client.get(f"{UPSTASH_URL}/get/{cache_key}")
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("result")
        if raw is None:
            print(f"[cache:MISS] {cache_key}")
            return None
        print(f"[cache:HIT] {cache_key}")
        return json.loads(raw)
    except Exception as exc:
        print(f"[cache:ERROR get] {cache_key} — {exc}")
        return None


def cache_set(
    cache_key: str,
    result:    dict,
    ticker:    str = "",
    form_type: str = "",
    tool_name: str = "",
    ttl_days:  int = CACHE_TTL_DAYS,
) -> None:
    """Store result in cache with TTL. Never raises."""
    if not _CACHE_ENABLED:
        return
    ttl_days = max(3, min(ttl_days, 7))
    ttl_secs = ttl_days * 86_400
    try:
        client = _get_sync_client()
        payload = json.dumps(result)
        # Upstash REST: POST body is the raw value appended as the last
        # SET argument; EX goes as a query param per Upstash's REST spec.
        resp = client.post(
            f"{UPSTASH_URL}/set/{cache_key}",
            params={"EX": ttl_secs},
            content=payload,
        )
        resp.raise_for_status()
        print(f"[cache:SAVE] {cache_key} (ttl={ttl_days}d)")
    except Exception as exc:
        print(f"[cache:ERROR set] {cache_key} — {exc}")


def cache_delete(cache_key: str) -> None:
    if not _CACHE_ENABLED:
        return
    try:
        client = _get_sync_client()
        client.get(f"{UPSTASH_URL}/del/{cache_key}")
    except Exception as exc:
        print(f"[cache:ERROR delete] {cache_key} — {exc}")


def cache_stats() -> dict:
    """Lightweight connectivity check — confirms the cache is reachable
    and configured. Uses the PING command via Upstash's path-based REST
    convention (REST_URL/ping)."""
    if not _CACHE_ENABLED:
        return {"enabled": False, "reason": "UPSTASH_REDIS_REST_URL/TOKEN not set"}
    try:
        client = _get_sync_client()
        resp = client.get(f"{UPSTASH_URL}/ping")
        resp.raise_for_status()
        return {"enabled": True, "ttl_days": CACHE_TTL_DAYS, "ping": resp.json().get("result")}
    except Exception as exc:
        return {"enabled": True, "ttl_days": CACHE_TTL_DAYS, "error": str(exc)}


def make_cache_key(tool: str, ticker: str, form_type: str, extra: str = "") -> str:
    """
    Build a consistent cache key.

    For tools that compare specific filings, pass the newer filing's
    accession_number (or filing_date as fallback) as `extra` so a new
    filing dropping doesn't silently return a stale cached result.
    Example: make_cache_key("compare_filings", "AAPL", "10-K", accession_number)
    """
    parts = [tool, ticker.upper(), form_type]
    if extra:
        parts.append(extra)
    return ":".join(parts)


def log_cache_event(event: str, cache_key: str) -> None:
    """Lightweight stdout logging so cache hits/misses are visible in Render logs."""
    print(f"[cache:{event}] {cache_key}")
