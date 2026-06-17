"""
core/proxy.py — RiskLens v2
=============================
DEF 14A (Proxy Statement) governance & compensation intelligence.

Extracts and scores governance risk signals: executive compensation,
related-party transactions, shareholder proposals, board structure,
and contested voting matters.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from core.fetcher import fetch_one_filing
from core.cache import cache_get, cache_set, make_cache_key

GOVERNANCE_SIGNALS = {
    "say-on-pay":                  2,
    "related party transaction":   1,
    "related-party transaction":   1,
    "golden parachute":            1,
    "shareholder proposal":        2,
    "proxy contest":               1,
    "dissident":                   1,
    "activist":                    1,
    "executive compensation":      3,
    "clawback":                    2,
    "poison pill":                 1,
    "staggered board":             2,
    "dual-class":                  2,
    "related person transaction":  1,
    "stock option repricing":      1,
    "excessive compensation":      1,
    "compensation consultant":     3,
    "say on golden parachute":     1,
}

_TIER_NAMES = {1: "critical", 2: "high", 3: "moderate"}


@dataclass
class GovernanceSignal:
    signal:   str
    tier:     int
    excerpt:  str


@dataclass
class ProxyAnalysis:
    ticker:               str
    filing_date:           str
    accession_number:       str
    pipeline_success:       bool
    failure_reason:          Optional[str] = None
    governance_signals:      list[GovernanceSignal] = field(default_factory=list)
    governance_risk_score:    float = 0.0
    summary:                  str = ""
    elapsed_seconds:           float = 0.0


async def analyze_proxy_filing(ticker: str) -> dict:
    start = time.monotonic()
    ticker = ticker.upper().strip()

    cache_key = make_cache_key("analyze_proxy", ticker, "DEF 14A")
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    filing = await fetch_one_filing(ticker, "DEF 14A")

    if not filing or not filing.fetch_success or not filing.html:
        return {
            "ticker": ticker, "pipeline_success": False,
            "failure_reason": f"Could not fetch DEF 14A for {ticker}. "
                               f"Not all companies file proxies on a predictable schedule.",
            "filing_date": None, "accession_number": None,
            "governance_signals": [], "governance_risk_score": 0.0,
            "summary": "", "elapsed_seconds": round(time.monotonic() - start, 2),
            "from_cache": False,
        }

    signals = _extract_governance_signals(filing.html)
    score   = sum({1: 5, 2: 3, 3: 1}.get(s.tier, 0) for s in signals)
    summary = _build_summary(ticker, filing.filing_date, signals, score)

    result = {
        "ticker": ticker, "pipeline_success": True, "failure_reason": None,
        "filing_date": filing.filing_date, "accession_number": filing.accession_number,
        "governance_signals": [
            {"signal": s.signal, "tier": s.tier, "materiality": _TIER_NAMES[s.tier], "excerpt": s.excerpt}
            for s in signals
        ],
        "governance_risk_score": round(score, 1),
        "summary": summary,
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "from_cache": False,
    }

    if signals:
        cache_set(cache_key, result, ticker=ticker, form_type="DEF 14A", tool_name="analyze_proxy")

    return result


def _extract_governance_signals(html: str) -> list[GovernanceSignal]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text_lower = text.lower()

    found: list[GovernanceSignal] = []
    for phrase, tier in GOVERNANCE_SIGNALS.items():
        idx = text_lower.find(phrase.lower())
        if idx == -1:
            continue
        start = max(0, idx - 60)
        end   = min(len(text), idx + len(phrase) + 80)
        excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
        found.append(GovernanceSignal(signal=phrase, tier=tier, excerpt=excerpt[:250]))

    return found


def _build_summary(ticker, filing_date, signals, score) -> str:
    if not signals:
        return f"{ticker}'s proxy statement ({filing_date}) shows no elevated governance signals."
    tier1 = [s.signal for s in signals if s.tier == 1]
    parts = [f"{ticker}'s proxy statement ({filing_date}) governance risk score: {score:.1f}."]
    if tier1:
        parts.append(f"Critical governance flags: {', '.join(tier1)}.")
    parts.append(f"{len(signals)} total governance signals detected.")
    return " ".join(parts)
