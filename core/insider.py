"""
core/insider.py — RiskLens v2
================================
Form 4 insider trading intelligence.

Tracks insider purchases/sales by officers, directors, and 10%+ owners.
Form 4 is XML-based (not free text), so this parses the structured XML
directly rather than running text extraction.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from core.fetcher import fetch_n_filings
from core.cache import cache_get, cache_set, make_cache_key


@dataclass
class InsiderTransaction:
    filing_date:      str
    insider_name:      str
    insider_title:      str
    transaction_code:    str   # P=Purchase, S=Sale, A=Award, etc.
    transaction_type:     str  # human-readable
    shares:                Optional[float]
    price_per_share:        Optional[float]
    shares_owned_after:       Optional[float]


@dataclass
class InsiderActivitySummary:
    ticker:                 str
    pipeline_success:        bool
    failure_reason:            Optional[str] = None
    transactions:              list[InsiderTransaction] = field(default_factory=list)
    net_shares_bought:           float = 0.0
    net_shares_sold:               float = 0.0
    buy_count:                       int = 0
    sell_count:                       int = 0
    summary:                          str = ""
    elapsed_seconds:                   float = 0.0


_CODE_LABELS = {
    "P": "Open market purchase",
    "S": "Open market sale",
    "A": "Award / grant",
    "M": "Option exercise",
    "F": "Tax withholding",
    "G": "Gift",
    "C": "Conversion",
    "D": "Disposition (non-sale)",
}


async def analyze_insider_filings(ticker: str, n_filings: int = 10) -> dict:
    start = time.monotonic()
    ticker = ticker.upper().strip()

    cache_key = make_cache_key("analyze_insider_activity", ticker, "4", str(n_filings))
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    filings = await fetch_n_filings(ticker, "4", n=n_filings)

    if not filings:
        return {
            "ticker": ticker, "pipeline_success": False,
            "failure_reason": f"No Form 4 filings found for {ticker} in the recent window.",
            "transactions": [], "net_shares_bought": 0.0, "net_shares_sold": 0.0,
            "buy_count": 0, "sell_count": 0, "summary": "",
            "elapsed_seconds": round(time.monotonic() - start, 2), "from_cache": False,
        }

    all_txns: list[InsiderTransaction] = []
    for f in filings:
        if not f.fetch_success or not f.html:
            continue
        all_txns.extend(_parse_form4_xml(f.html, f.filing_date))

    net_bought = sum(t.shares or 0 for t in all_txns if t.transaction_code == "P")
    net_sold   = sum(t.shares or 0 for t in all_txns if t.transaction_code == "S")
    buy_count  = sum(1 for t in all_txns if t.transaction_code == "P")
    sell_count = sum(1 for t in all_txns if t.transaction_code == "S")

    summary = _build_summary(ticker, net_bought, net_sold, buy_count, sell_count, len(filings))

    result = {
        "ticker": ticker, "pipeline_success": True, "failure_reason": None,
        "transactions": [
            {
                "filing_date": t.filing_date, "insider_name": t.insider_name,
                "insider_title": t.insider_title, "transaction_code": t.transaction_code,
                "transaction_type": t.transaction_type, "shares": t.shares,
                "price_per_share": t.price_per_share, "shares_owned_after": t.shares_owned_after,
            }
            for t in all_txns
        ],
        "net_shares_bought": net_bought, "net_shares_sold": net_sold,
        "buy_count": buy_count, "sell_count": sell_count,
        "summary": summary,
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "from_cache": False,
    }

    if all_txns:
        cache_set(cache_key, result, ticker=ticker, form_type="4", tool_name="analyze_insider_activity")

    return result


def _parse_form4_xml(html: str, filing_date: str) -> list[InsiderTransaction]:
    """Form 4 documents are XML wrapped in HTML on EDGAR — parse leniently."""
    txns: list[InsiderTransaction] = []
    try:
        soup = BeautifulSoup(html, "lxml")

        name_tag  = soup.find("rptownername")
        title_tag = soup.find("officertitle")
        insider_name  = name_tag.get_text(strip=True) if name_tag else "Unknown"
        insider_title = title_tag.get_text(strip=True) if title_tag else "Insider"

        for tx in soup.find_all("nonderivativetransaction"):
            code_tag  = tx.find("transactioncode")
            shares_tag = tx.find("transactionshares")
            price_tag  = tx.find("transactionpricepershare")
            after_tag   = tx.find("sharesownedfollowingtransaction")

            code = code_tag.get_text(strip=True) if code_tag else ""
            shares = _safe_float(shares_tag.find("value").get_text(strip=True)) if shares_tag and shares_tag.find("value") else None
            price  = _safe_float(price_tag.find("value").get_text(strip=True)) if price_tag and price_tag.find("value") else None
            after  = _safe_float(after_tag.find("value").get_text(strip=True)) if after_tag and after_tag.find("value") else None

            txns.append(InsiderTransaction(
                filing_date=filing_date, insider_name=insider_name, insider_title=insider_title,
                transaction_code=code, transaction_type=_CODE_LABELS.get(code, "Other"),
                shares=shares, price_per_share=price, shares_owned_after=after,
            ))
    except Exception:
        pass
    return txns


def _safe_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return None


def _build_summary(ticker, bought, sold, buy_count, sell_count, n_filings) -> str:
    net = bought - sold
    direction = "net buying" if net > 0 else "net selling" if net < 0 else "balanced activity"
    return (
        f"{ticker} insider activity across {n_filings} recent Form 4 filings: "
        f"{buy_count} purchase transaction(s) totaling {bought:,.0f} shares, "
        f"{sell_count} sale transaction(s) totaling {sold:,.0f} shares. "
        f"Net position: {direction} ({net:+,.0f} shares)."
    )
