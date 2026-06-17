"""
core/financial_context.py — RiskLens v2
==========================================
Financial Context Engine.

Pulls quantitative validation metrics from SEC's Company Facts API
(data.sec.gov/api/xbrl/companyfacts/CIK##########.json) so qualitative
risk signals can be checked against real numbers — e.g. "declining
revenue" language is far more material if Current Ratio is also falling.

Never crashes. Missing values, malformed XBRL tags, or API rate limits
all degrade gracefully to None with a logged reason.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from core.fetcher import fetch_with_retries, EDGAR_BASE

TIMEOUT_FACTS = 25.0

# XBRL US-GAAP tags we look for, in priority order (companies vary in
# which exact tag they tag their financials with across periods/restatements)
_TAG_CANDIDATES = {
    "accounts_receivable":  ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "inventory":            ["InventoryNet", "InventoryFinishedGoodsNetOfReserves"],
    "capex":                ["PaymentsToAcquirePropertyPlantAndEquipment",
                              "PaymentsForCapitalImprovements"],
    "current_assets":       ["AssetsCurrent"],
    "current_liabilities":  ["LiabilitiesCurrent"],
    "revenue":               ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "net_income":            ["NetIncomeLoss"],
    "cash_and_equivalents":  ["CashAndCashEquivalentsAtCarryingValue"],
    "total_debt":            ["LongTermDebtNoncurrent", "DebtCurrent"],
}


@dataclass
class FinancialContext:
    cik:                    str
    fetch_success:           bool
    failure_reason:           Optional[str] = None
    accounts_receivable:      Optional[float] = None
    inventory:                 Optional[float] = None
    capex:                     Optional[float] = None
    current_assets:            Optional[float] = None
    current_liabilities:       Optional[float] = None
    current_ratio:             Optional[float] = None
    revenue:                   Optional[float] = None
    net_income:                Optional[float] = None
    cash_and_equivalents:       Optional[float] = None
    total_debt:                 Optional[float] = None
    period_end:                 Optional[str] = None
    missing_metrics:            list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "accounts_receivable":  self.accounts_receivable,
            "inventory":             self.inventory,
            "capex":                 self.capex,
            "current_assets":        self.current_assets,
            "current_liabilities":   self.current_liabilities,
            "current_ratio":         self.current_ratio,
            "revenue":                self.revenue,
            "net_income":             self.net_income,
            "cash_and_equivalents":    self.cash_and_equivalents,
            "total_debt":              self.total_debt,
            "period_end":              self.period_end,
            "missing_metrics":         self.missing_metrics,
            "fetch_success":           self.fetch_success,
            "failure_reason":          self.failure_reason,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def get_financial_context(
    cik: str, timeout_total: float = TIMEOUT_FACTS,
) -> FinancialContext:
    """
    Fetch and parse SEC Company Facts (XBRL) for the given CIK.
    Returns FinancialContext with whatever metrics could be resolved —
    never raises, always returns a usable object.
    """
    deadline = time.monotonic() + timeout_total
    cik_padded = str(cik).zfill(10)
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik_padded}.json"

    try:
        resp = await fetch_with_retries(url, timeout_total, deadline)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return FinancialContext(
            cik=cik_padded, fetch_success=False,
            failure_reason=f"Company Facts fetch failed: {type(exc).__name__}: {exc}",
        )

    facts = data.get("facts", {}).get("us-gaap", {})
    if not facts:
        return FinancialContext(
            cik=cik_padded, fetch_success=False,
            failure_reason="No us-gaap facts found for this CIK (may be a foreign filer or holding co).",
        )

    resolved: dict[str, Optional[float]] = {}
    period_end: Optional[str] = None
    missing: list[str] = []

    for metric_key, tag_candidates in _TAG_CANDIDATES.items():
        value, p_end = _resolve_latest_value(facts, tag_candidates)
        resolved[metric_key] = value
        if value is None:
            missing.append(metric_key)
        elif period_end is None:
            period_end = p_end

    current_ratio = None
    ca = resolved.get("current_assets")
    cl = resolved.get("current_liabilities")
    if ca is not None and cl is not None and cl != 0:
        current_ratio = round(ca / cl, 3)

    return FinancialContext(
        cik=cik_padded,
        fetch_success=True,
        failure_reason=None,
        accounts_receivable=resolved.get("accounts_receivable"),
        inventory=resolved.get("inventory"),
        capex=resolved.get("capex"),
        current_assets=ca,
        current_liabilities=cl,
        current_ratio=current_ratio,
        revenue=resolved.get("revenue"),
        net_income=resolved.get("net_income"),
        cash_and_equivalents=resolved.get("cash_and_equivalents"),
        total_debt=resolved.get("total_debt"),
        period_end=period_end,
        missing_metrics=missing,
    )


# ---------------------------------------------------------------------------
# XBRL value resolution
# ---------------------------------------------------------------------------

def _resolve_latest_value(
    facts: dict, tag_candidates: list[str],
) -> tuple[Optional[float], Optional[str]]:
    """
    Try each candidate XBRL tag in order. Within a tag, pick the most
    recent USD value reported (companies often have several units/periods).
    """
    for tag in tag_candidates:
        tag_data = facts.get(tag)
        if not tag_data:
            continue
        usd_values = tag_data.get("units", {}).get("USD", [])
        if not usd_values:
            continue
        try:
            best = max(usd_values, key=lambda v: v.get("end", ""))
            val  = best.get("val")
            end  = best.get("end")
            if val is not None:
                return float(val), end
        except Exception:
            continue
    return None, None


# ---------------------------------------------------------------------------
# Risk-relevance interpretation helpers (used by scorer / report tools)
# ---------------------------------------------------------------------------

def interpret_current_ratio(ratio: Optional[float]) -> str:
    if ratio is None:
        return "Current ratio unavailable — liquidity risk cannot be quantitatively validated."
    if ratio < 1.0:
        return f"Current ratio {ratio:.2f} is below 1.0 — liabilities exceed liquid assets, elevating liquidity/going-concern risk."
    if ratio < 1.5:
        return f"Current ratio {ratio:.2f} is tight — limited liquidity buffer."
    return f"Current ratio {ratio:.2f} indicates adequate short-term liquidity."


def interpret_capex_trend(capex: Optional[float], revenue: Optional[float]) -> str:
    if capex is None or revenue is None or revenue == 0:
        return "CapEx-to-revenue ratio unavailable."
    pct = (capex / revenue) * 100
    if pct > 15:
        return f"CapEx is {pct:.1f}% of revenue — heavy infrastructure/strategic investment, consistent with AI/capacity buildout risk language."
    return f"CapEx is {pct:.1f}% of revenue — within typical range."
