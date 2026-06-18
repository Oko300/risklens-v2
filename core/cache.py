"""
core/cache.py — RiskLens v2
=============================
Upstash Redis caching layer using the official upstash-redis SDK.

WHY UPSTASH: Render's free-tier filesystem is ephemeral — the SQLite
.db file reset on every redeploy, which silently erased all cached
results and made caching look broken even when the code was correct.
Upstash's free tier gives a persistent, hosted Redis instance reachable
over REST HTTP (no TCP connection pooling needed), so the cache now
survives redeploys and restarts.

WHY SDK INSTEAD OF RAW HTTPX: The previous implementation used raw
httpx to call POST /set/{key} with the JSON payload as the request body.
That is not a valid Upstash REST endpoint — the correct REST format is
a JSON POST to the base URL with a Redis command array as the body
(e.g. ["SET", "key", "value", "EX", 604800]). The upstash-redis SDK
handles this correctly, including base64 encoding, retries, and TTL,
so we delegate to the SDK rather than re-implementing the protocol.

Setup (one-time):
  1. Create a free database at https://console.upstash.com
  2. Copy the REST URL and REST TOKEN from the database details page
  3. Set these env vars on Render (or in your .env for local dev):
       UPSTASH_REDIS_REST_URL
       UPSTASH_REDIS_REST_TOKEN
  4. Add  upstash-redis  to requirements.txt
  5. If unset, this module degrades gracefully to "no caching" rather
     than crashing — every tool still works, just without the speedup.

Public API (unchanged from prior version — no tool code needs to change):
  cache_get(cache_key)          -> Optional[dict]
  cache_set(cache_key, result, ticker, form_type, tool_name, ttl_days)
  cache_delete(cache_key)
  cache_stats()                 -> dict
  make_cache_key(tool, ticker, form_type, extra) -> str
  log_cache_event(event, cache_key)
"""

import json
import os
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_TTL_DAYS = max(3, min(int(os.getenv("CACHE_TTL_DAYS", "7")), 7))
CACHE_TTL_SECS = CACHE_TTL_DAYS * 86_400

UPSTASH_URL   = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

_CACHE_ENABLED = bool(UPSTASH_URL and UPSTASH_TOKEN)

# ---------------------------------------------------------------------------
# SDK client — lazy singleton, created once on first use
# ---------------------------------------------------------------------------

_redis = None  # upstash_redis.Redis instance


def _get_client():
    """Return (or lazily create) the synchronous Upstash Redis client."""
    global _redis
    if _redis is None:
        from upstash_redis import Redis
        _redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
    return _redis


# ---------------------------------------------------------------------------
# Startup log
# ---------------------------------------------------------------------------

if _CACHE_ENABLED:
    print(
        f"[cache:init] Upstash Redis caching ENABLED "
        f"(TTL={CACHE_TTL_DAYS}d) at {UPSTASH_URL}"
    )
else:
    print(
        "[cache:init] Upstash Redis NOT configured "
        "(UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN missing) "
        "— caching disabled, tools will run uncached."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cache_get(cache_key: str) -> Optional[dict]:
    """Return cached result dict if present, else None. Never raises."""
    if not _CACHE_ENABLED:
        return None
    try:
        client = _get_client()
        raw = client.get(cache_key)
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
    """Store result dict in Upstash Redis with TTL. Never raises."""
    if not _CACHE_ENABLED:
        return
    ttl_days = max(3, min(ttl_days, 7))
    ttl_secs = ttl_days * 86_400
    try:
        client = _get_client()
        # SDK .set(key, value, ex=seconds) builds the correct Redis command
        # array and POSTs it to Upstash REST — value must be a string.
        client.set(cache_key, json.dumps(result), ex=ttl_secs)
        print(f"[cache:SAVE] {cache_key} (ttl={ttl_days}d)")
    except Exception as exc:
        print(f"[cache:ERROR set] {cache_key} — {exc}")


def cache_delete(cache_key: str) -> None:
    """Delete a cache entry. Never raises."""
    if not _CACHE_ENABLED:
        return
    try:
        client = _get_client()
        client.delete(cache_key)
        print(f"[cache:DELETE] {cache_key}")
    except Exception as exc:
        print(f"[cache:ERROR delete] {cache_key} — {exc}")


def cache_stats() -> dict:
    """
    Lightweight connectivity check — confirms the cache is reachable.
    Returns a dict with enabled status, TTL, and ping result.
    """
    if not _CACHE_ENABLED:
        return {
            "enabled": False,
            "reason": "UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN not set",
        }
    try:
        from upstash_redis import Redis
        client = _get_client()
        # ping() returns "PONG" on success
        result = client.ping()
        return {
            "enabled": True,
            "ttl_days": CACHE_TTL_DAYS,
            "ping": result,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "ttl_days": CACHE_TTL_DAYS,
            "error": str(exc),
        }


def make_cache_key(tool: str, ticker: str, form_type: str, extra: str = "") -> str:
    """
    Build a consistent, collision-resistant cache key.

    For tools that compare specific filings, pass the newer filing's
    accession_number (or filing_date as fallback) as `extra` so that
    when a new filing drops, the stale cached result is not returned.

    Examples:
        make_cache_key("compare_filings", "AAPL", "10-K")
        make_cache_key("analyze_risk_trends", "MSFT", "10-Q", "4")
    """
    parts = [tool, ticker.upper(), form_type]
    if extra:
        parts.append(extra)
    return ":".join(parts)


def log_cache_event(event: str, cache_key: str) -> None:
    """Lightweight stdout logging so cache hits/misses are visible in Render logs."""
    print(f"[cache:{event}] {cache_key}")