"""
core/cache.py — RiskLens v2
=============================
Upstash Redis caching layer (raw httpx REST calls, no SDK dependency).

WHY RAW HTTPX, NOT THE upstash-redis SDK: the SDK adds a dependency with
its own internal HTTP client and default timeout behavior we don't
control. A misconfigured token or transient network issue inside the SDK
can hang far longer than acceptable inside an MCP tool call. This module
uses httpx directly with an explicit, short timeout on every request, so
a cache failure NEVER blocks a tool — at worst it adds a few hundred
milliseconds before falling through to "no cache, proceed normally."

WHY UPSTASH: Render's free-tier filesystem is ephemeral — a SQLite .db
file resets on every redeploy, silently erasing all cached results.
Upstash's free tier is a persistent, hosted Redis instance reachable
over REST HTTP, so the cache survives redeploys and restarts.

DEFENSIVE TOKEN HANDLING: Render's "upload .env file" feature does not
strip surrounding quote marks the way python-dotenv does locally — if a
value was entered as `"abc123"` (with literal quotes) it gets stored
including those quote characters, which silently breaks Bearer auth.
This module strips stray leading/trailing quotes from both the URL and
token at load time, so a quoted value still works correctly even before
you've had a chance to clean it up in Render's dashboard.

Setup (one-time):
  1. Create a free database at https://console.upstash.com
  2. Copy the REST URL and REST TOKEN from the database details page
  3. Set these env vars on Render — type/paste the raw values directly
     into the Key/Value fields in the Environment tab (no surrounding
     quotes needed; this module strips them defensively anyway):
       UPSTASH_REDIS_REST_URL
       UPSTASH_REDIS_REST_TOKEN
  4. If unset or unreachable, this module degrades gracefully to
     "no caching" rather than crashing or hanging — every tool still
     works, just without the speedup on repeat calls.

Public API (unchanged — no tool code needs to change):
  cache_get(cache_key)          -> Optional[dict]
  cache_set(cache_key, result, ticker, form_type, tool_name, ttl_days)
  cache_delete(cache_key)
  cache_stats()                 -> dict
  make_cache_key(tool, ticker, form_type, extra) -> str
  log_cache_event(event, cache_key)
"""

import json
import os
import urllib.parse
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_TTL_DAYS = max(3, min(int(os.getenv("CACHE_TTL_DAYS", "7")), 7))
CACHE_TTL_SECS = CACHE_TTL_DAYS * 86_400


def _strip_stray_quotes(value: str) -> str:
    """
    Defensively remove a single pair of leading/trailing quote marks.

    Render's 'upload .env file' UI sometimes stores values literally as
    written in the file, including any quote characters the file author
    included around the value (e.g. KEY="abc123" becomes the value
    '"abc123"' instead of 'abc123'). This strips one matching pair of
    straight or curly quotes from each end if present, so a quoted
    value still authenticates correctly.
    """
    value = value.strip()
    quote_pairs = [('"', '"'), ("'", "'")]
    for left, right in quote_pairs:
        if len(value) >= 2 and value.startswith(left) and value.endswith(right):
            return value[1:-1].strip()
    return value


UPSTASH_URL   = _strip_stray_quotes(os.getenv("UPSTASH_REDIS_REST_URL", "")).rstrip("/")
UPSTASH_TOKEN = _strip_stray_quotes(os.getenv("UPSTASH_REDIS_REST_TOKEN", ""))

_CACHE_ENABLED = bool(UPSTASH_URL and UPSTASH_TOKEN)

# Short, explicit timeout on every request — a cache failure must NEVER
# be allowed to hang a tool call. 4 seconds is generous for a REST round
# trip to Upstash; if it takes longer than that, something is wrong and
# we fall through to "no cache" rather than blocking the user.
_REQUEST_TIMEOUT = httpx.Timeout(connect=3.0, read=4.0, write=3.0, pool=3.0)

_sync_client: Optional[httpx.Client] = None


def _get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None or _sync_client.is_closed:
        _sync_client = httpx.Client(
            timeout=_REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        )
    return _sync_client


# ---------------------------------------------------------------------------
# Startup log — makes the actual config state visible in Render logs
# without ever printing the token itself
# ---------------------------------------------------------------------------

if _CACHE_ENABLED:
    print(f"[cache:init] Upstash Redis caching ENABLED (TTL={CACHE_TTL_DAYS}d) at {UPSTASH_URL}")
else:
    print(
        "[cache:init] Upstash Redis NOT configured (UPSTASH_REDIS_REST_URL / "
        "UPSTASH_REDIS_REST_TOKEN missing or empty) — caching disabled, "
        "tools will run uncached."
    )


def _url_safe_key(cache_key: str) -> str:
    """
    Explicitly URL-encode the cache key before it goes into any HTTP path.

    Form types like 'DEF 14A' or 'SC 13D' contain spaces, and relying on
    implicit auto-encoding is fragile — this makes encoding explicit and
    identical for every request type.
    """
    return urllib.parse.quote(cache_key, safe="")


# ---------------------------------------------------------------------------
# Public API — synchronous, matching tool call sites (no await needed)
# ---------------------------------------------------------------------------

def cache_get(cache_key: str) -> Optional[dict]:
    """Return cached result dict if valid, else None. Never raises, never hangs."""
    if not _CACHE_ENABLED:
        return None
    try:
        client = _get_sync_client()
        safe_key = _url_safe_key(cache_key)
        resp = client.get(f"{UPSTASH_URL}/get/{safe_key}")
        if resp.status_code == 401:
            print(f"[cache:ERROR get] {cache_key} — 401 Unauthorized: check "
                  f"UPSTASH_REDIS_REST_TOKEN has no stray quote marks")
            return None
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            print(f"[cache:ERROR get] {cache_key} — Upstash error: {data['error']}")
            return None
        raw = data.get("result")
        if raw is None:
            print(f"[cache:MISS] {cache_key}")
            return None
        print(f"[cache:HIT] {cache_key}")
        return json.loads(raw)
    except httpx.TimeoutException:
        print(f"[cache:ERROR get] {cache_key} — Upstash request timed out, proceeding without cache")
        return None
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
    """Store result in cache with TTL. Never raises, never hangs."""
    if not _CACHE_ENABLED:
        return
    ttl_days = max(3, min(ttl_days, 7))
    ttl_secs = ttl_days * 86_400
    try:
        client = _get_sync_client()
        safe_key = _url_safe_key(cache_key)
        payload = json.dumps(result)
        resp = client.post(
            f"{UPSTASH_URL}/set/{safe_key}",
            params={"EX": ttl_secs},
            content=payload,
        )
        if resp.status_code == 401:
            print(f"[cache:ERROR set] {cache_key} — 401 Unauthorized: check "
                  f"UPSTASH_REDIS_REST_TOKEN has no stray quote marks")
            return
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            print(f"[cache:ERROR set] {cache_key} — Upstash error: {data['error']}")
            return
        print(f"[cache:SAVE] {cache_key} (ttl={ttl_days}d)")
    except httpx.TimeoutException:
        print(f"[cache:ERROR set] {cache_key} — Upstash request timed out, result not cached")
    except Exception as exc:
        print(f"[cache:ERROR set] {cache_key} — {exc}")


def cache_delete(cache_key: str) -> None:
    if not _CACHE_ENABLED:
        return
    try:
        client = _get_sync_client()
        safe_key = _url_safe_key(cache_key)
        client.get(f"{UPSTASH_URL}/del/{safe_key}")
        print(f"[cache:DELETE] {cache_key}")
    except Exception as exc:
        print(f"[cache:ERROR delete] {cache_key} — {exc}")


def cache_stats() -> dict:
    """Lightweight connectivity check — confirms the cache is reachable and authenticated."""
    if not _CACHE_ENABLED:
        return {"enabled": False, "reason": "UPSTASH_REDIS_REST_URL/TOKEN not set"}
    try:
        client = _get_sync_client()
        resp = client.get(f"{UPSTASH_URL}/ping")
        if resp.status_code == 401:
            return {"enabled": True, "ttl_days": CACHE_TTL_DAYS,
                     "error": "401 Unauthorized — check token has no stray quote marks"}
        resp.raise_for_status()
        return {"enabled": True, "ttl_days": CACHE_TTL_DAYS, "ping": resp.json().get("result")}
    except httpx.TimeoutException:
        return {"enabled": True, "ttl_days": CACHE_TTL_DAYS, "error": "request timed out"}
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