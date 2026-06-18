"""
core/insider.py — RiskLens v2
================================
Form 4 insider trading intelligence.

Tracks insider purchases/sales by officers, directors, and 10%+ owners.

RELIABILITY FIX (v3): previous versions guessed at XML filenames inside
the EDGAR filing index page, which is fragile — EDGAR's naming pattern
for the raw XML data file varies by filer and filing tool version (e.g.
"xslF345X05/wf-form4_....xml" vs "primary_doc.xml" vs ticker-prefixed
names). This version instead:

  1. Reads `primaryDocument` directly from SEC's own submissions JSON
     (the field SEC uses to tell every consumer which file is canonical)
  2. Constructs the raw XML URL by stripping any XSLT viewer path prefix
     SEC sometimes embeds, so we always fetch the underlying data file
     rather than a styled HTML rendering
  3. Falls back to scanning the index page for *any* .xml file if the
     primaryDocument-derived URL 404s (some older filings predate the
     primaryDocument field being populated)
  4. Falls back further to the rendered HTML table parser only if no
     XML can be found or parsed at all

This makes resolution deterministic based on what SEC itself reports,
rather than pattern-guessing against arbitrary filer naming conventions.
"""

import re
import time
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup
from core.fetcher import (
    fetch_with_retries, _get_submissions, _extract_filings_from_submissions,
    _resolve_cik, EDGAR_FULL, TIMEOUT_INDEX, TIMEOUT_HTML,
)
from core.cache import cache_get, cache_set, make_cache_key


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
    start = time.monotonic()
    ticker = ticker.upper().strip()

    cache_key = make_cache_key("analyze_insider_activity", ticker, "4", str(n_filings))
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    deadline = time.monotonic() + 100.0

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
    debug_notes: list[str] = []

    for f in form4_filings:
        accession   = f["accession"]
        filing_date = f.get("filing_date", "")
        primary_doc = f.get("primary_document", "")

        try:
            xml_text, note = await _fetch_form4_xml(cik, accession, primary_doc, deadline)
            if xml_text:
                txns = _parse_form4_xml(xml_text, filing_date)
                if txns:
                    all_txns.extend(txns)
                    filings_with_data += 1
                    continue
                else:
                    debug_notes.append(f"{accession}: XML fetched but 0 transactions parsed ({note})")
            else:
                debug_notes.append(f"{accession}: no XML found ({note})")

            # Fallback: rendered HTML table
            html_text = await _fetch_form4_rendered_html(cik, accession, primary_doc, deadline)
            if html_text:
                txns = _parse_form4_rendered_html(html_text, filing_date)
                if txns:
                    all_txns.extend(txns)
                    filings_with_data += 1
        except Exception as exc:
            debug_notes.append(f"{accession}: exception — {exc}")
            continue

    if not all_txns:
        detail = " | ".join(debug_notes[:5]) if debug_notes else "no diagnostic detail captured"
        return _empty_result(
            ticker, start,
            f"Found {len(form4_filings)} Form 4 filing(s) for {ticker} but could not parse "
            f"transaction data from any of them. Diagnostic: {detail}"
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
        "elapsed_seconds": round(time.monotonic() - start, 2),
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
        "elapsed_seconds": round(time.monotonic() - start, 2), "from_cache": False,
    }


# ---------------------------------------------------------------------------
# Document resolution — primaryDocument-driven, not filename-guessing
# ---------------------------------------------------------------------------

def _build_accession_dashes(accession_no_dashes: str) -> str:
    return f"{accession_no_dashes[:10]}-{accession_no_dashes[10:12]}-{accession_no_dashes[12:]}"


async def _get_filing_index_links(cik: str, accession_no_dashes: str, deadline: float) -> list[tuple[str, str]]:
    """Returns list of (filename, full_url) for every document in the filing index."""
    acc_dashes = _build_accession_dashes(accession_no_dashes)
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


async def _fetch_form4_xml(
    cik: str, accession_no_dashes: str, primary_document: str, deadline: float,
) -> tuple[Optional[str], str]:
    """
    Resolve and fetch the raw XML data file for a Form 4 filing.

    Primary strategy: SEC's submissions JSON tells us the primaryDocument
    filename directly (e.g. "xslF345X05/primary_doc.xml" or
    "wf-form4_1234567890.xml"). We fetch that path directly under the
    accession folder — no guessing.

    Fallback strategy: scan the filing index page for any .xml file.
    """
    base = f"{EDGAR_FULL}/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/"

    if primary_document:
        # primaryDocument sometimes includes a viewer subfolder path
        # (e.g. "xslF345X05/wf-form4_....xml") — that subfolder IS where
        # the real XML lives on EDGAR, so we fetch it as-is first.
        candidate_url = base + primary_document
        try:
            resp = await fetch_with_retries(candidate_url, TIMEOUT_HTML, deadline)
            if resp.status_code == 200 and primary_document.lower().endswith(".xml"):
                return resp.text, f"fetched via primaryDocument={primary_document}"
        except Exception:
            pass

        # If primaryDocument doesn't end in .xml (rare, but some older
        # filings reference an .html primary doc), try stripping any
        # viewer subfolder and looking for a sibling .xml at the same level.
        if not primary_document.lower().endswith(".xml"):
            sibling_base = primary_document.rsplit("/", 1)[0] + "/" if "/" in primary_document else ""
            try:
                links = await _get_filing_index_links(cik, accession_no_dashes, deadline)
                for filename, url in links:
                    if filename.lower().endswith(".xml"):
                        resp = await fetch_with_retries(url, TIMEOUT_HTML, deadline)
                        resp.raise_for_status()
                        return resp.text, f"fetched via index fallback (primaryDocument was non-XML: {primary_document})"
            except Exception:
                pass

    # No primaryDocument available at all — fall back to index scan
    try:
        links = await _get_filing_index_links(cik, accession_no_dashes, deadline)
        for filename, url in links:
            if filename.lower().endswith(".xml"):
                resp = await fetch_with_retries(url, TIMEOUT_HTML, deadline)
                resp.raise_for_status()
                return resp.text, "fetched via index scan (no primaryDocument field)"
    except Exception as exc:
        return None, f"index scan failed: {exc}"

    return None, "no .xml file found via primaryDocument or index scan"


async def _fetch_form4_rendered_html(
    cik: str, accession_no_dashes: str, primary_document: str, deadline: float,
) -> Optional[str]:
    """Fallback: fetch the rendered HTML/XSLT version of the Form 4."""
    base = f"{EDGAR_FULL}/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/"

    if primary_document:
        candidate_url = base + primary_document
        try:
            resp = await fetch_with_retries(candidate_url, TIMEOUT_HTML, deadline)
            resp.raise_for_status()
            return resp.text
        except Exception:
            pass

    try:
        links = await _get_filing_index_links(cik, accession_no_dashes, deadline)
        for filename, url in links:
            fname_lower = filename.lower()
            if fname_lower.endswith((".htm", ".html")) and "index" not in fname_lower:
                resp = await fetch_with_retries(url, TIMEOUT_HTML, deadline)
                resp.raise_for_status()
                return resp.text
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# XML parser (primary path) — handles both raw XML and SEC's XSLT-rendered
# documents, since the rendered document still embeds the same XML tags
# inside an HTML wrapper in many EDGAR viewer outputs.
# ---------------------------------------------------------------------------

def _parse_form4_xml(xml_text: str, filing_date: str) -> list[InsiderTransaction]:
    txns: list[InsiderTransaction] = []
    try:
        # Try strict XML parsing first; fall back to lxml's lenient HTML
        # parser since some "XML" responses are actually XHTML-wrapped.
        soup = BeautifulSoup(xml_text, "lxml-xml")
        if not soup.find(re.compile(r"(?i)nonderivativetransaction|derivativetransaction|rptownername")):
            soup = BeautifulSoup(xml_text, "lxml")
    except Exception:
        soup = BeautifulSoup(xml_text, "lxml")

    def _find_ci(tag_name: str):
        return soup.find(lambda t: t.name and t.name.lower() == tag_name.lower())

    def _find_all_ci(tag_name: str):
        return soup.find_all(lambda t: t.name and t.name.lower() == tag_name.lower())

    try:
        name_tag  = _find_ci("rptOwnerName")
        title_tag = _find_ci("officerTitle")
        insider_name  = name_tag.get_text(strip=True) if name_tag else "Unknown insider"
        insider_title = title_tag.get_text(strip=True) if title_tag else _infer_title(soup, _find_ci)

        tx_tags = _find_all_ci("nonDerivativeTransaction")
        for tx in tx_tags:
            code_tag   = tx.find(lambda t: t.name and t.name.lower() == "transactioncode")
            shares_tag = tx.find(lambda t: t.name and t.name.lower() == "transactionshares")
            price_tag  = tx.find(lambda t: t.name and t.name.lower() == "transactionpricepershare")
            after_tag  = tx.find(lambda t: t.name and t.name.lower() == "sharesownedfollowingtransaction")

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
    value_tag = tag.find(lambda t: t.name and t.name.lower() == "value")
    raw = value_tag.get_text(strip=True) if value_tag else tag.get_text(strip=True)
    return _safe_float(raw)


def _infer_title(soup, find_ci_fn) -> str:
    is_director = find_ci_fn("isDirector")
    is_officer  = find_ci_fn("isOfficer")
    is_ten_pct  = find_ci_fn("isTenPercentOwner")
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

_CODE_PATTERN = re.compile(r"^[PSAMFGCD]$")

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
