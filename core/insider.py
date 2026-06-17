"""
core/insider.py — RiskLens v2
================================
Form 4 insider trading intelligence.

Tracks insider purchases/sales by officers, directors, and 10%+ owners.

RELIABILITY FIX: Form 4 filings on EDGAR expose two documents per filing:
  1. The raw XML data file (e.g. xslF345X05/...xml or wk-form4_....xml)
  2. A human-readable HTML rendering of the same data (the "primary
     document" most index-resolvers grab by default)
The generic fetcher's index resolver was picking #2, which uses completely
different markup (rendered tables, not <nonDerivativeTransaction> tags),
so the original XML-tag parser found nothing. This module now resolves
its own document list per filing and explicitly prefers the XML file,
with a structured fallback parser for the rendered HTML table when no
XML file is present.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from core.fetcher import (
    fetch_with_retries, _get_submissions, _extract_filings_from_submissions,
    _resolve_cik, EDGAR_BASE, EDGAR_FULL, TIMEOUT_INDEX, TIMEOUT_HTML,
)
from core.cache import cache_get, cache_set, make_cache_key

import time as _time


@dataclass
class InsiderTransaction:
    filing_date:         str
    insider_name:         str
    insider_title:         str
    transaction_code:       str   # P=Purchase, S=Sale, A=Award, etc.
    transaction_type:        str  # human-readable
    shares:                   Optional[float]
    price_per_share:           Optional[float]
    shares_owned_after:          Optional[float]


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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def analyze_insider_filings(ticker: str, n_filings: int = 10) -> dict:
    start = _time.monotonic()
    ticker = ticker.upper().strip()

    cache_key = make_cache_key("analyze_insider_activity", ticker, "4", str(n_filings))
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    deadline = _time.monotonic() + 90.0

    try:
        cik = await _resolve_cik(ticker, deadline)
    except Exception as exc:
        return _empty_result(ticker, start, f"Could not resolve ticker to CIK: {exc}")

    try:
        data = await _get_submissions(cik, deadline)
    except Exception as exc:
        return _empty_result(ticker, start, f"Could not fetch SEC submissions: {exc}")

    form4_filings = _extract_filings_from_submissions(data, "4")[:max(1, min(n_filings, 25))]

    if not form4_filings:
        return _empty_result(
            ticker, start,
            f"No Form 4 filings found for {ticker} in the recent submissions window. "
            f"This can happen for companies with very low insider trading activity recently."
        )

    all_txns: list[InsiderTransaction] = []
    filings_with_data = 0

    for f in form4_filings:
        accession = f["accession"]
        filing_date = f.get("filing_date", "")
        try:
            xml_text = await _fetch_form4_xml(cik, accession, deadline)
            if xml_text:
                txns = _parse_form4_xml(xml_text, filing_date)
                if txns:
                    all_txns.extend(txns)
                    filings_with_data += 1
                    continue
            # Fallback: try the rendered HTML table if no usable XML
            html_text = await _fetch_form4_rendered_html(cik, accession, deadline)
            if html_text:
                txns = _parse_form4_rendered_html(html_text, filing_date)
                if txns:
                    all_txns.extend(txns)
                    filings_with_data += 1
        except Exception:
            continue

    if not all_txns:
        return _empty_result(
            ticker, start,
            f"Found {len(form4_filings)} Form 4 filing(s) for {ticker} but could not parse "
            f"transaction data from any of them. This filer may use a non-standard XML schema."
        )

    net_bought = sum(t.shares or 0 for t in all_txns if t.transaction_code == "P")
    net_sold   = sum(t.shares or 0 for t in all_txns if t.transaction_code == "S")
    buy_count  = sum(1 for t in all_txns if t.transaction_code == "P")
    sell_count = sum(1 for t in all_txns if t.transaction_code == "S")

    summary = _build_summary(ticker, net_bought, net_sold, buy_count, sell_count, filings_with_data, len(form4_filings))

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
        "elapsed_seconds": round(_time.monotonic() - start, 2),
        "from_cache": False,
    }

    cache_set(cache_key, result, ticker=ticker, form_type="4", tool_name="analyze_insider_activity")
    return result


def _empty_result(ticker: str, start: float, reason: str) -> dict:
    return {
        "ticker": ticker, "pipeline_success": False,
        "failure_reason": reason,
        "transactions": [], "net_shares_bought": 0.0, "net_shares_sold": 0.0,
        "buy_count": 0, "sell_count": 0, "summary": "",
        "elapsed_seconds": round(_time.monotonic() - start, 2), "from_cache": False,
    }


# ---------------------------------------------------------------------------
# Document resolution — Form 4 specific (prefers raw XML over rendered HTML)
# ---------------------------------------------------------------------------

async def _get_filing_index_links(cik: str, accession_no_dashes: str, deadline: float) -> list[tuple[str, str]]:
    """Returns list of (filename, full_url) for every document in the filing index."""
    acc_dashes = (
        f"{accession_no_dashes[:10]}-"
        f"{accession_no_dashes[10:12]}-"
        f"{accession_no_dashes[12:]}"
    )
    index_url = (
        f"{EDGAR_FULL}/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/"
        f"{acc_dashes}-index.htm"
    )
    resp = await fetch_with_retries(index_url, TIMEOUT_INDEX, deadline)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    links = []
    for a in soup.select("table a[href]"):
        href = a["href"]
        filename = href.rsplit("/", 1)[-1]
        full_url = href if href.startswith("http") else f"{EDGAR_FULL}{href}" if href.startswith("/") else (
            f"{EDGAR_FULL}/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{href}"
        )
        links.append((filename, full_url))
    return links


async def _fetch_form4_xml(cik: str, accession_no_dashes: str, deadline: float) -> Optional[str]:
    """Find and fetch the raw XML data file for a Form 4 filing."""
    links = await _get_filing_index_links(cik, accession_no_dashes, deadline)
    xml_url = None
    for filename, url in links:
        fname_lower = filename.lower()
        if fname_lower.endswith(".xml") and "form4" not in fname_lower.replace("_", ""):
            # Most Form 4 XML files are named like "xslF345X0X/...xml" or "wf-form4_....xml"
            xml_url = url
            break
    if not xml_url:
        for filename, url in links:
            if filename.lower().endswith(".xml"):
                xml_url = url
                break
    if not xml_url:
        return None
    try:
        resp = await fetch_with_retries(xml_url, TIMEOUT_HTML, deadline)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


async def _fetch_form4_rendered_html(cik: str, accession_no_dashes: str, deadline: float) -> Optional[str]:
    """Fallback: fetch the rendered HTML version of the Form 4."""
    links = await _get_filing_index_links(cik, accession_no_dashes, deadline)
    html_url = None
    for filename, url in links:
        fname_lower = filename.lower()
        if fname_lower.endswith((".htm", ".html")) and "index" not in fname_lower:
            html_url = url
            break
    if not html_url:
        return None
    try:
        resp = await fetch_with_retries(html_url, TIMEOUT_HTML, deadline)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


# ---------------------------------------------------------------------------
# XML parser (primary path)
# ---------------------------------------------------------------------------

def _parse_form4_xml(xml_text: str, filing_date: str) -> list[InsiderTransaction]:
    txns: list[InsiderTransaction] = []
    try:
        soup = BeautifulSoup(xml_text, "xml")

        name_tag  = soup.find("rptOwnerName") or soup.find("rptownername")
        title_tag = soup.find("officerTitle") or soup.find("officertitle")
        insider_name  = name_tag.get_text(strip=True) if name_tag else "Unknown insider"
        insider_title = title_tag.get_text(strip=True) if title_tag else _infer_title(soup)

        tx_tags = soup.find_all("nonDerivativeTransaction") or soup.find_all("nonderivativetransaction")
        for tx in tx_tags:
            code_tag   = tx.find("transactionCode") or tx.find("transactioncode")
            shares_tag = tx.find("transactionShares") or tx.find("transactionshares")
            price_tag  = tx.find("transactionPricePerShare") or tx.find("transactionpricepershare")
            after_tag  = tx.find("sharesOwnedFollowingTransaction") or tx.find("sharesownedfollowingtransaction")

            code = code_tag.get_text(strip=True) if code_tag else ""
            shares = _extract_value(shares_tag)
            price  = _extract_value(price_tag)
            after  = _extract_value(after_tag)

            if not code:
                continue

            txns.append(InsiderTransaction(
                filing_date=filing_date, insider_name=insider_name, insider_title=insider_title,
                transaction_code=code, transaction_type=_CODE_LABELS.get(code, "Other"),
                shares=shares, price_per_share=price, shares_owned_after=after,
            ))
    except Exception:
        pass
    return txns


def _extract_value(tag) -> Optional[float]:
    if tag is None:
        return None
    value_tag = tag.find("value")
    raw = value_tag.get_text(strip=True) if value_tag else tag.get_text(strip=True)
    return _safe_float(raw)


def _infer_title(soup) -> str:
    is_director = soup.find("isDirector") or soup.find("isdirector")
    is_officer  = soup.find("isOfficer") or soup.find("isofficer")
    is_ten_pct  = soup.find("isTenPercentOwner") or soup.find("istenpercentowner")
    parts = []
    if is_director and is_director.get_text(strip=True) == "1":
        parts.append("Director")
    if is_officer and is_officer.get_text(strip=True) == "1":
        parts.append("Officer")
    if is_ten_pct and is_ten_pct.get_text(strip=True) == "1":
        parts.append("10% Owner")
    return ", ".join(parts) if parts else "Insider"


def _safe_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Rendered HTML parser (fallback path)
# ---------------------------------------------------------------------------

_CODE_PATTERN = re.compile(r"\b([PSAMFGCD])\b")

def _parse_form4_rendered_html(html_text: str, filing_date: str) -> list[InsiderTransaction]:
    """
    Best-effort parse of EDGAR's rendered Form 4 HTML table when no XML
    file is available. Table structure varies, so this scans for rows
    containing a recognizable transaction code plus a numeric shares value.
    """
    txns: list[InsiderTransaction] = []
    try:
        soup = BeautifulSoup(html_text, "lxml")
        text_blob = soup.get_text(separator="|", strip=True)

        name_match = re.search(r"Name and Address of Reporting Person\*?\s*\|+([A-Z][A-Za-z .,'-]{2,60})", text_blob)
        insider_name = name_match.group(1).strip() if name_match else "Unknown insider"

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) < 4:
                    continue
                code_cell = next((c for c in cells if _CODE_PATTERN.fullmatch(c)), None)
                if not code_cell:
                    continue
                numeric_cells = [c for c in cells if re.match(r"^[\d,]+\.?\d*$", c.replace(",", ""))]
                if not numeric_cells:
                    continue
                shares = _safe_float(numeric_cells[0]) if numeric_cells else None
                if shares is None:
                    continue
                txns.append(InsiderTransaction(
                    filing_date=filing_date, insider_name=insider_name, insider_title="Insider",
                    transaction_code=code_cell, transaction_type=_CODE_LABELS.get(code_cell, "Other"),
                    shares=shares, price_per_share=None, shares_owned_after=None,
                ))
    except Exception:
        pass
    return txns


def _build_summary(ticker, bought, sold, buy_count, sell_count, filings_with_data, total_filings) -> str:
    net = bought - sold
    direction = "net buying" if net > 0 else "net selling" if net < 0 else "balanced activity"
    coverage_note = (
        f"(parsed {filings_with_data}/{total_filings} filings successfully)"
        if filings_with_data < total_filings else ""
    )
    return (
        f"{ticker} insider activity across {total_filings} recent Form 4 filings {coverage_note}: "
        f"{buy_count} purchase transaction(s) totaling {bought:,.0f} shares, "
        f"{sell_count} sale transaction(s) totaling {sold:,.0f} shares. "
        f"Net position: {direction} ({net:+,.0f} shares)."
    ).strip()
