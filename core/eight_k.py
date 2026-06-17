"""
core/eight_k.py — RiskLens v2
================================
8-K material event extraction + risk-materiality scoring.

8-K filings disclose material events within 4 business days. Each filing
contains one or more numbered "Item" sections. This module extracts each
Item section and maps it to a risk-materiality tier so 8-K events feed
into the same risk-intelligence framework as Risk Factors / MD&A.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from core.fetcher import fetch_n_filings, FilingHTMLMeta
from core.cache import cache_get, cache_set, make_cache_key


# ---------------------------------------------------------------------------
# 8-K Item taxonomy — mapped to risk materiality tiers
# ---------------------------------------------------------------------------
# Tier 1 = CRITICAL  | Tier 2 = HIGH | Tier 3 = MODERATE | Tier 4 = LOW

ITEM_TAXONOMY: dict[str, dict] = {
    "1.01": {"label": "Entry into a Material Definitive Agreement",        "tier": 2},
    "1.02": {"label": "Termination of a Material Definitive Agreement",    "tier": 2},
    "1.03": {"label": "Bankruptcy or Receivership",                        "tier": 1},
    "1.04": {"label": "Mine Safety - Reporting of Shutdowns and Patterns",  "tier": 3},
    "2.01": {"label": "Completion of Acquisition or Disposition of Assets","tier": 2},
    "2.02": {"label": "Results of Operations and Financial Condition",     "tier": 2},
    "2.03": {"label": "Creation of a Direct Financial Obligation",         "tier": 2},
    "2.04": {"label": "Triggering Events / Acceleration of Obligations",   "tier": 1},
    "2.05": {"label": "Costs Associated with Exit / Disposal Activities",  "tier": 2},
    "2.06": {"label": "Material Impairments",                              "tier": 1},
    "3.01": {"label": "Notice of Delisting / Failure to Satisfy Listing",  "tier": 1},
    "3.02": {"label": "Unregistered Sales of Equity Securities",           "tier": 3},
    "3.03": {"label": "Material Modification to Rights of Security Holders","tier": 2},
    "4.01": {"label": "Changes in Registrant's Certifying Accountant",     "tier": 1},
    "4.02": {"label": "Non-Reliance on Previously Issued Financials",      "tier": 1},
    "5.01": {"label": "Changes in Control of Registrant",                  "tier": 1},
    "5.02": {"label": "Departure/Election of Directors or Officers",       "tier": 2},
    "5.03": {"label": "Amendments to Articles of Incorporation/Bylaws",    "tier": 3},
    "5.04": {"label": "Temporary Suspension of Trading Under Benefit Plans","tier": 2},
    "5.05": {"label": "Amendments to Code of Ethics",                      "tier": 4},
    "5.07": {"label": "Submission of Matters to a Vote of Security Holders","tier": 3},
    "6.01": {"label": "ABS Informational and Computational Material",      "tier": 4},
    "7.01": {"label": "Regulation FD Disclosure",                          "tier": 3},
    "8.01": {"label": "Other Events",                                      "tier": 3},
    "9.01": {"label": "Financial Statements and Exhibits",                 "tier": 4},
}

_TIER_NAMES = {1: "critical", 2: "high", 3: "moderate", 4: "low"}


@dataclass
class EightKItem:
    item_number: str
    label: str
    tier: int
    materiality: str
    excerpt: str


@dataclass
class EightKFiling:
    filing_date:      str
    accession_number: str
    document_url:     str
    items:            list[EightKItem]
    fetch_success:    bool
    failure_reason:   Optional[str] = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def analyze_8k_filings(ticker: str, n_filings: int = 5) -> dict:
    """
    Fetch and analyze the N most recent 8-K filings for a ticker.
    Returns a dict ready for EightKOutput construction.
    Cached for 7 days (configurable via CACHE_TTL_DAYS).
    """
    start = time.monotonic()
    ticker = ticker.upper().strip()

    cache_key = make_cache_key("analyze_8k_events", ticker, "8-K", str(n_filings))
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    filings = await fetch_n_filings(ticker, "8-K", n=n_filings)

    if not filings:
        return {
            "ticker": ticker, "pipeline_success": False,
            "failure_reason": f"No 8-K filings found for {ticker}.",
            "events": [], "filing_count": 0, "highest_risk_event": None,
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "from_cache": False,
        }

    parsed_filings: list[EightKFiling] = []
    for f in filings:
        if not f.fetch_success or not f.html:
            parsed_filings.append(EightKFiling(
                filing_date=f.filing_date, accession_number=f.accession_number,
                document_url=f.document_url, items=[],
                fetch_success=False, failure_reason=f.failure_reason,
            ))
            continue
        items = _extract_items(f.html)
        parsed_filings.append(EightKFiling(
            filing_date=f.filing_date, accession_number=f.accession_number,
            document_url=f.document_url, items=items, fetch_success=True,
        ))

    # Flatten events for output, sorted newest-first
    events = []
    highest_tier = 5
    highest_label = None
    for pf in parsed_filings:
        for item in pf.items:
            events.append({
                "filing_date":      pf.filing_date,
                "accession_number": pf.accession_number,
                "item_number":      item.item_number,
                "label":            item.label,
                "materiality":      item.materiality,
                "excerpt":          item.excerpt,
            })
            if item.tier < highest_tier:
                highest_tier = item.tier
                highest_label = f"Item {item.item_number} — {item.label} ({pf.filing_date})"

    result = {
        "ticker": ticker, "pipeline_success": True, "failure_reason": None,
        "events": events, "filing_count": len(parsed_filings),
        "highest_risk_event": highest_label,
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "from_cache": False,
    }

    if events:
        cache_set(cache_key, result, ticker=ticker, form_type="8-K", tool_name="analyze_8k_events")

    return result


# ---------------------------------------------------------------------------
# Item extraction
# ---------------------------------------------------------------------------

_ITEM_RE = re.compile(r"item\s+(\d\.\d{2})\b", re.IGNORECASE)


def _extract_items(html: str) -> list[EightKItem]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)

    matches = list(_ITEM_RE.finditer(text))
    items: list[EightKItem] = []
    seen: set[str] = set()

    for i, m in enumerate(matches):
        item_num = m.group(1)
        if item_num not in ITEM_TAXONOMY:
            continue
        if item_num in seen:
            continue
        seen.add(item_num)

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(text), start + 2000)
        excerpt = text[start:end].strip().replace("\n", " ")
        excerpt = re.sub(r"\s+", " ", excerpt)[:300]

        spec = ITEM_TAXONOMY[item_num]
        items.append(EightKItem(
            item_number=item_num,
            label=spec["label"],
            tier=spec["tier"],
            materiality=_TIER_NAMES[spec["tier"]],
            excerpt=excerpt,
        ))

    return items
