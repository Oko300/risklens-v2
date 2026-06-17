"""
core/cache.py — RiskLens v2
=============================
SQLite + TTL caching system.
- Stores analysis results as JSON (no raw HTML)
- Auto-expires after TTL days (default 7)
- Auto-cleans expired records on every write
- Thread-safe using check_same_thread=False
"""

import sqlite3
import json
import time
import os
import threading
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_TTL_DAYS   = max(3, min(int(os.getenv("CACHE_TTL_DAYS", "7")), 7))
CACHE_TTL_SECS   = CACHE_TTL_DAYS * 86_400
CACHE_DB_PATH    = os.getenv("CACHE_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "risklens_cache.db"
)
CACHE_DB_PATH    = os.path.abspath(CACHE_DB_PATH)

_db_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    with _db_lock:
        if _conn is None:
            _conn = sqlite3.connect(CACHE_DB_PATH, check_same_thread=False)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("""
                CREATE TABLE IF NOT EXISTS analysis_cache (
                    cache_key  TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL,
                    ticker      TEXT,
                    form_type   TEXT,
                    tool_name   TEXT
                )
            """)
            _conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires
                ON analysis_cache(expires_at)
            """)
            _conn.commit()
            print(f"[cache:init] SQLite cache ready at {CACHE_DB_PATH} (TTL={CACHE_TTL_DAYS}d)")
    return _conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cache_get(cache_key: str) -> Optional[dict]:
    """Return cached result dict if valid, else None."""
    try:
        conn = _get_conn()
        now  = time.time()
        with _db_lock:
            row = conn.execute(
                "SELECT result_json FROM analysis_cache "
                "WHERE cache_key=? AND expires_at>?",
                (cache_key, now)
            ).fetchone()
        if row:
            print(f"[cache:HIT] {cache_key}")
            return json.loads(row[0])
        print(f"[cache:MISS] {cache_key}")
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
    """Store result in cache. Auto-cleans expired entries."""
    ttl_days = max(3, min(ttl_days, 7))
    try:
        conn     = _get_conn()
        now      = time.time()
        expires  = now + ttl_days * 86_400
        with _db_lock:
            conn.execute("""
                INSERT OR REPLACE INTO analysis_cache
                (cache_key, result_json, created_at, expires_at, ticker, form_type, tool_name)
                VALUES (?,?,?,?,?,?,?)
            """, (cache_key, json.dumps(result), now, expires, ticker, form_type, tool_name))
            # Clean expired records on every write
            deleted = conn.execute("DELETE FROM analysis_cache WHERE expires_at<=?", (now,)).rowcount
            conn.commit()
        print(f"[cache:SAVE] {cache_key} (ttl={ttl_days}d, expired_cleaned={deleted})")
    except Exception as exc:
        print(f"[cache:ERROR set] {cache_key} — {exc}")


def cache_delete(cache_key: str) -> None:
    try:
        conn = _get_conn()
        with _db_lock:
            conn.execute("DELETE FROM analysis_cache WHERE cache_key=?", (cache_key,))
            conn.commit()
    except Exception:
        pass


def cache_stats() -> dict:
    """Return cache statistics — useful for debugging."""
    try:
        conn = _get_conn()
        now  = time.time()
        with _db_lock:
            total   = conn.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()[0]
            valid   = conn.execute(
                "SELECT COUNT(*) FROM analysis_cache WHERE expires_at>?", (now,)
            ).fetchone()[0]
            expired = total - valid
        return {"total": total, "valid": valid, "expired": expired,
                "ttl_days": CACHE_TTL_DAYS, "db_path": CACHE_DB_PATH}
    except Exception as e:
        return {"error": str(e)}


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
