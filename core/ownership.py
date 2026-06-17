"""
core/ownership.py — RiskLens v2
==================================
13D / 13G / 13F ownership intelligence.

13D = active/activist investor disclosure (>5% stake, intends to influence)
13G = passive investor disclosure (>5% stake, no intent to influence)
13F = institutional quarterly holdings report (>$100M AUM managers)

RELIABILITY FIX:
1. EDGAR amendments file under "SC 13D/A", "SC 13G/A", "13F-HR/A" — the
   prior version only checked the unamended form types, missing most
   real-world activity since amendments are extremely common.
2. Percent-ownership regex now matches whole-number, one-decimal, and
   two-decimal percentages (5%, 5.1%, 5.12%) instead of only X.XX%.
3. 13F-HR filings report a manager's FULL portfolio (every company they
   hold), not a single-company percentage — the prior code tried to
   regex a percentage out of 13F-HR text, which doesn't exist there.
   13F-HR filings are now flagged as portfolio-level disclosures rather
   than incorrectly parsed for a single ownership percentage.
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
    "replace management", "shareholder value", "engage in discussions",
    "nominate", "withhold vote",
]

# Each base form type plus its common amendment suffix
_FORM_TYPE_GROUPS = {
    "SC 13D":  ["SC 13D", "SC 13D/A"],
    "SC 13G":  ["SC 13G", "SC 13G/A"],
    "13F-HR":  ["13F-HR", "13F-HR/A"],
}


@dataclass
class OwnershipFiling:
    filing_date:        str
    form_type:           str
    filer_name:           str
    percent_ownership:      Optional[float]
    is_portfolio_filing:      bool   # True for 13F-HR (no single % applies)
    is_activist_signal:         bool
    activist_phrases_found:       list[str]


async def analyze_ownership_filings(ticker: str, n_filings: int = 6) -> dict:
    start = time.monotonic()
    ticker = ticker.upper().strip()

    cache_key = make_cache_key("analyze_ownership", ticker, "13D-13G-13F", str(n_filings))
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    all_filings: list[OwnershipFiling] = []
    form_types_tried: list[str] = []

    for group_label, variants in _FORM_TYPE_GROUPS.items():
        for form_type in variants:
            form_types_tried.append(form_type)
            try:
                filings = await fetch_n_filings(ticker, form_type, n=n_filings)
            except Exception:
                continue
            for f in filings:
                if not f.fetch_success or not f.html:
                    continue
                all_filings.append(_parse_ownership_filing(f.html, f.filing_date, form_type))

    if not all_filings:
        return {
            "ticker": ticker, "pipeline_success": False,
            "failure_reason": (
                f"No 13D, 13G, or 13F-HR filings (including amendments) found for {ticker}. "
                f"This is common for smaller companies with no activist or "
                f"institutional >5% holders currently on file with the SEC."
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
                "is_portfolio_filing": f.is_portfolio_filing,
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

    is_13f = form_type.startswith("13F-HR")

    pct = None
    if not is_13f:
        # Matches 5%, 5.1%, 5.12% — EDGAR filers use all three formats
        pct_match = re.search(r"\b(\d{1,2}(?:\.\d{1,2})?)\s*%", text)
        if pct_match:
            try:
                pct = float(pct_match.group(1))
            except Exception:
                pct = None

    filer_name = "Unknown filer"
    name_match = re.search(
        r"name of reporting person[s]?\s*:?\s*([A-Z][A-Za-z0-9 ,.&'-]{2,80})",
        text, re.IGNORECASE,
    )
    if name_match:
        filer_name = name_match.group(1).strip()
    elif is_13f:
        # 13F-HR filer name usually appears near "Filer" or in the cover page header
        alt_match = re.search(r"(?:filed by|filer)\s*:?\s*([A-Z][A-Za-z0-9 ,.&'-]{2,80})", text, re.IGNORECASE)
        if alt_match:
            filer_name = alt_match.group(1).strip()

    found_phrases = [p for p in _ACTIVIST_PHRASES if p in text_lower]
    is_activist = form_type.startswith("SC 13D") or len(found_phrases) > 0

    return OwnershipFiling(
        filing_date=filing_date, form_type=form_type, filer_name=filer_name,
        percent_ownership=pct, is_portfolio_filing=is_13f,
        is_activist_signal=is_activist, activist_phrases_found=found_phrases,
    )


def _build_summary(ticker, filings, activist_count) -> str:
    by_type: dict[str, int] = {}
    for f in filings:
        by_type[f.form_type] = by_type.get(f.form_type, 0) + 1
    type_summary = ", ".join(f"{count} {ft}" for ft, count in by_type.items())

    activist_filers = [f.filer_name for f in filings if f.is_activist_signal and f.filer_name != "Unknown filer"]
    portfolio_filers = [f.filer_name for f in filings if f.is_portfolio_filing and f.filer_name != "Unknown filer"]

    parts = [f"{ticker} ownership filings found: {type_summary}."]
    if activist_count > 0:
        names = ", ".join(sorted(set(activist_filers))[:3]) if activist_filers else "unnamed filer(s)"
        parts.append(f"{activist_count} filing(s) show activist-style language or active (13D) intent — including {names}.")
    else:
        parts.append("No activist signals detected — ownership filings appear passive.")
    if portfolio_filers:
        parts.append(f"Institutional 13F-HR filers disclosing this position: {', '.join(sorted(set(portfolio_filers))[:3])}.")
    return " ".join(parts)
