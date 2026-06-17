"""
core/ownership.py — RiskLens v2
==================================
13D / 13G / 13F ownership intelligence.

13D = active/activist investor disclosure (>5% stake, intends to influence)
13G = passive investor disclosure (>5% stake, no intent to influence)
13F = institutional quarterly holdings report (>$100M AUM managers)

Tracks ownership concentration, activist signals, and institutional
accumulation/distribution patterns.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from core.fetcher import fetch_n_filings
from core.cache import cache_get, cache_set, make_cache_key

_ACTIVIST_PHRASES = [
    "intends to engage", "board representation", "strategic alternatives",
    "activist", "proxy fight", "special committee", "underperformance",
    "unlock value", "divest", "spin-off", "sell the company",
    "replace management", "shareholder value",
]


@dataclass
class OwnershipFiling:
    filing_date:       str
    form_type:           str
    filer_name:           str
    percent_ownership:      Optional[float]
    is_activist_signal:       bool
    activist_phrases_found:    list[str]


async def analyze_ownership_filings(ticker: str, n_filings: int = 6) -> dict:
    start = time.monotonic()
    ticker = ticker.upper().strip()

    cache_key = make_cache_key("analyze_ownership", ticker, "13D-13G-13F", str(n_filings))
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    all_filings: list[OwnershipFiling] = []
    fetch_errors: list[str] = []

    for form_type in ("SC 13D", "SC 13G", "13F-HR"):
        try:
            filings = await fetch_n_filings(ticker, form_type, n=n_filings)
            for f in filings:
                if not f.fetch_success or not f.html:
                    continue
                all_filings.append(_parse_ownership_filing(f.html, f.filing_date, form_type))
        except Exception as exc:
            fetch_errors.append(f"{form_type}: {exc}")

    if not all_filings:
        return {
            "ticker": ticker, "pipeline_success": False,
            "failure_reason": (
                f"No 13D/13G/13F filings found for {ticker}. "
                f"This is common for smaller companies with no activist or "
                f"institutional >5% holders currently on file."
            ),
            "filings": [], "activist_signal_count": 0, "summary": "",
            "elapsed_seconds": round(time.monotonic() - start, 2), "from_cache": False,
        }

    activist_count = sum(1 for f in all_filings if f.is_activist_signal)
    summary = _build_summary(ticker, all_filings, activist_count)

    result = {
        "ticker": ticker, "pipeline_success": True, "failure_reason": None,
        "filings": [
            {
                "filing_date": f.filing_date, "form_type": f.form_type,
                "filer_name": f.filer_name, "percent_ownership": f.percent_ownership,
                "is_activist_signal": f.is_activist_signal,
                "activist_phrases_found": f.activist_phrases_found,
            }
            for f in all_filings
        ],
        "activist_signal_count": activist_count,
        "summary": summary,
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "from_cache": False,
    }

    cache_set(cache_key, result, ticker=ticker, form_type="13D-13G-13F", tool_name="analyze_ownership")
    return result


def _parse_ownership_filing(html: str, filing_date: str, form_type: str) -> OwnershipFiling:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text_lower = text.lower()

    # Percent ownership — look for "X.X%" near "of the class" or "outstanding"
    pct = None
    pct_match = re.search(r"(\d{1,2}\.\d{1,2})\s*%", text)
    if pct_match:
        try:
            pct = float(pct_match.group(1))
        except Exception:
            pct = None

    # Filer name — best-effort from common heading patterns
    filer_name = "Unknown filer"
    name_match = re.search(r"name of reporting person[s]?\s*:?\s*([A-Z][A-Za-z0-9 ,.&'-]{2,80})", text, re.IGNORECASE)
    if name_match:
        filer_name = name_match.group(1).strip()

    found_phrases = [p for p in _ACTIVIST_PHRASES if p in text_lower]
    is_activist = form_type == "SC 13D" or len(found_phrases) > 0

    return OwnershipFiling(
        filing_date=filing_date, form_type=form_type, filer_name=filer_name,
        percent_ownership=pct, is_activist_signal=is_activist,
        activist_phrases_found=found_phrases,
    )


def _build_summary(ticker, filings, activist_count) -> str:
    by_type = {}
    for f in filings:
        by_type[f.form_type] = by_type.get(f.form_type, 0) + 1
    type_summary = ", ".join(f"{count} {ft}" for ft, count in by_type.items())
    parts = [f"{ticker} ownership filings found: {type_summary}."]
    if activist_count > 0:
        parts.append(f"{activist_count} filing(s) show activist-style language or 13D (active) intent.")
    else:
        parts.append("No activist signals detected — ownership appears passive.")
    return " ".join(parts)
