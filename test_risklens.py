"""
test_risklens.py — Standalone integration test for RiskLens v2
Tests all three tool pipelines directly (no MCP server required).
Ticker: AAPL  |  Forms: 10-K and 10-Q
"""

import asyncio
import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.fetcher   import fetch_two_filings, fetch_n_filings, fetch_one_filing
from core.extractor import extract_sections_cached
from core.delta     import compute_delta
from core.scorer    import score_sections

TICKER    = "AAPL"
SEP       = "─" * 60


def fmt(label, value, indent=2):
    pad = " " * indent
    print(f"{pad}{label}: {value}")


def section_summary(label, section):
    print(f"\n  [{label}]")
    fmt("extraction_success", section.extraction_success)
    fmt("method",             section.method.value if hasattr(section.method,"value") else section.method)
    fmt("confidence",         f"{section.confidence_score:.2f}")
    fmt("char_count",         section.char_count)
    if section.failure_reason:
        fmt("failure_reason", section.failure_reason)
    if section.coverage_gap_note:
        fmt("coverage_gap",   section.coverage_gap_note[:100] + "…")


# ============================================================
# TOOL 1: compare_filings  (10-K)
# ============================================================

async def test_compare_filings():
    print(f"\n{SEP}")
    print("TOOL 1: compare_filings  (AAPL 10-K)")
    print(SEP)
    t0 = time.monotonic()

    fetch_result = await fetch_two_filings(TICKER, "10-K")

    if not fetch_result.pipeline_success:
        print(f"  ✗ FETCH FAILED: {fetch_result.failure_reason}")
        return False

    newer_meta = fetch_result.newer
    older_meta = fetch_result.older
    fmt("newer filing", f"{newer_meta.filing_date}  acc={newer_meta.accession_number}")
    fmt("older filing", f"{older_meta.filing_date}  acc={older_meta.accession_number}")

    newer_ext, older_ext = await asyncio.gather(
        extract_sections_cached(fetch_result.newer_html or "", newer_meta.accession_number,
                                newer_meta.filing_date, "10-K"),
        extract_sections_cached(fetch_result.older_html or "", older_meta.accession_number,
                                older_meta.filing_date, "10-K"),
    )

    print("\n  Newer extraction:")
    section_summary("risk_factors", newer_ext.risk_factors)
    section_summary("mda",          newer_ext.mda)

    delta = compute_delta(
        older_risk=older_ext.risk_factors.text,
        newer_risk=newer_ext.risk_factors.text,
        older_mda=older_ext.mda.text,
        newer_mda=newer_ext.mda.text,
    )

    print("\n  Delta:")
    fmt("risk_factors magnitude", delta.risk_factors.magnitude.value)
    fmt("risk_factors pct_changed", f"{delta.risk_factors.pct_changed*100:.1f}%")
    fmt("mda magnitude",           delta.mda.magnitude.value)
    fmt("mda pct_changed",         f"{delta.mda.pct_changed*100:.1f}%")

    scoring = score_sections(
        newer_risk_text=newer_ext.risk_factors.text,
        older_risk_text=older_ext.risk_factors.text,
        newer_mda_text=newer_ext.mda.text,
        older_mda_text=older_ext.mda.text,
        risk_delta=delta.risk_factors,
        mda_delta=delta.mda,
    )

    print("\n  Scoring:")
    fmt("overall_materiality", scoring.overall_materiality.value.upper())
    fmt("risk_factors",        f"{scoring.risk_factors.materiality.value}  score={scoring.risk_factors.raw_score}")
    fmt("mda",                 f"{scoring.mda.materiality.value}  score={scoring.mda.raw_score}")
    fmt("top_signals",         ", ".join(scoring.top_signals[:5]))

    elapsed = time.monotonic() - t0
    print(f"\n  ✓ PASSED  ({elapsed:.1f}s)")
    return True


# ============================================================
# TOOL 2: analyze_risk_trends  (10-K, 3 filings)
# ============================================================

async def test_risk_trends():
    print(f"\n{SEP}")
    print("TOOL 2: analyze_risk_trends  (AAPL 10-K, n=3)")
    print(SEP)
    t0 = time.monotonic()

    filings = await fetch_n_filings(TICKER, "10-K", n=3)

    if not filings:
        print("  ✗ FETCH FAILED: no filings returned")
        return False

    fmt("filings fetched", len(filings))
    for f in filings:
        status = "✓" if f.fetch_success else "✗"
        fmt(f"  {status} {f.filing_date}", f"acc={f.accession_number}  html={'yes' if f.html else 'NO'}")

    # Extract + score each filing
    valid = [f for f in filings if f.fetch_success and f.html]
    if len(valid) < 2:
        print("  ✗ Not enough valid filings for trend")
        return False

    extraction_tasks = [
        extract_sections_cached(f.html, f.accession_number, f.filing_date, "10-K")
        for f in valid
    ]
    extractions = await asyncio.gather(*extraction_tasks)

    print("\n  Trend points (oldest → newest):")
    paired = list(zip(valid, extractions))
    paired.reverse()

    all_signal_sets = []
    for filing_meta, ext in paired:
        score = score_sections(
            newer_risk_text=ext.risk_factors.text, older_risk_text=None,
            newer_mda_text=ext.mda.text,           older_mda_text=None,
        )
        signals = {h.signal for h in score.risk_factors.tier1_hits + score.risk_factors.tier2_hits}
        all_signal_sets.append(signals)
        fmt(filing_meta.filing_date,
            f"risk={score.risk_factors.materiality.value}({score.risk_factors.raw_score:.1f})  "
            f"mda={score.mda.materiality.value}({score.mda.raw_score:.1f})")

    # Signal diff between consecutive filings
    print("\n  Signal changes across filings:")
    for i in range(1, len(all_signal_sets)):
        new_sigs     = sorted(all_signal_sets[i] - all_signal_sets[i-1])
        removed_sigs = sorted(all_signal_sets[i-1] - all_signal_sets[i])
        date = paired[i][0].filing_date
        if new_sigs:     fmt(f"  NEW at {date}",     ", ".join(new_sigs[:5]))
        if removed_sigs: fmt(f"  REMOVED at {date}", ", ".join(removed_sigs[:5]))

    elapsed = time.monotonic() - t0
    print(f"\n  ✓ PASSED  ({elapsed:.1f}s)")
    return True


# ============================================================
# TOOL 3: categorize_risks  (10-K)
# ============================================================

RISK_TAXONOMY = {
    "financial":          ["liquidity","cash flow","debt","leverage","going concern","net loss","default"],
    "legal_regulatory":   ["litigation","class action","SEC","DOJ","regulatory","compliance","settlement"],
    "cybersecurity":      ["cybersecurity","data breach","ransomware","unauthorized access","privacy"],
    "operational":        ["supply chain","disruption","key personnel","restructuring","outage"],
    "market_competitive": ["competition","market share","pricing pressure","customer concentration"],
    "macro_geopolitical": ["recession","inflation","interest rate","tariff","sanctions","geopolitical"],
    "strategic":          ["acquisition","integration risk","execution risk","merger"],
    "technology":         ["artificial intelligence","cloud","obsolescence","intellectual property"],
    "esg_climate":        ["climate change","ESG","carbon","sustainability","environmental"],
    "reputational":       ["reputational","brand","public perception","trust"],
}


async def test_categorize_risks():
    print(f"\n{SEP}")
    print("TOOL 3: categorize_risks  (AAPL 10-K)")
    print(SEP)
    t0 = time.monotonic()

    filing = await fetch_one_filing(TICKER, "10-K")

    if not filing or not filing.fetch_success or not filing.html:
        print("  ✗ FETCH FAILED")
        return False

    fmt("filing date",       filing.filing_date)
    fmt("accession",         filing.accession_number)
    fmt("html_byte_length",  filing.html_byte_length)

    ext = await extract_sections_cached(filing.html, filing.accession_number,
                                        filing.filing_date, "10-K")
    rf = ext.risk_factors

    if not rf.extraction_success or not rf.text:
        print(f"  ✗ Risk Factors extraction failed: {rf.failure_reason}")
        return False

    fmt("risk_factors chars", rf.char_count)
    fmt("extraction method",  rf.method.value if hasattr(rf.method,"value") else rf.method)

    # Categorize
    text_lower = rf.text.lower()
    results = []
    for domain, keywords in RISK_TAXONOMY.items():
        matched = [kw for kw in keywords if kw in text_lower]
        if matched:
            results.append((domain, len(matched), matched))

    results.sort(key=lambda x: -x[1])

    print(f"\n  Risk categories identified: {len(results)} / {len(RISK_TAXONOMY)}")
    print(f"  {'Domain':<25}  {'Signals':>7}  Top keywords")
    print(f"  {'─'*25}  {'─'*7}  {'─'*30}")
    for domain, count, matched in results:
        print(f"  {domain:<25}  {count:>7}  {', '.join(matched[:4])}")

    # Executive summary
    total_signals = sum(c for _,c,_ in results)
    tier1_domains = ["financial","legal_regulatory","cybersecurity"]
    tier1_found   = [d for d,_,_ in results if d in tier1_domains]
    print(f"\n  Executive summary:")
    fmt("total_domains",  len(results))
    fmt("total_signals",  total_signals)
    fmt("tier1_domains",  ", ".join(tier1_found) if tier1_found else "none")
    if results:
        top = results[0]
        fmt("dominant_theme", f"{top[0]} ({top[1]} signals: {', '.join(top[2][:4])})")

    elapsed = time.monotonic() - t0
    print(f"\n  ✓ PASSED  ({elapsed:.1f}s)")
    return True


# ============================================================
# Main runner
# ============================================================

async def main():
    print("=" * 60)
    print("  RiskLens v2 — Integration Test Suite")
    print(f"  Ticker: {TICKER}")
    print("=" * 60)

    results = {}

    try:
        results["compare_filings"]   = await test_compare_filings()
    except Exception as e:
        print(f"  ✗ compare_filings crashed: {e}")
        import traceback; traceback.print_exc()
        results["compare_filings"] = False

    try:
        results["analyze_risk_trends"] = await test_risk_trends()
    except Exception as e:
        print(f"  ✗ analyze_risk_trends crashed: {e}")
        import traceback; traceback.print_exc()
        results["analyze_risk_trends"] = False

    try:
        results["categorize_risks"]  = await test_categorize_risks()
    except Exception as e:
        print(f"  ✗ categorize_risks crashed: {e}")
        import traceback; traceback.print_exc()
        results["categorize_risks"] = False

    print(f"\n{'=' * 60}")
    print("  RESULTS")
    print("=" * 60)
    all_passed = True
    for tool, passed in results.items():
        icon = "✓" if passed else "✗"
        print(f"  {icon}  {tool}")
        if not passed: all_passed = False

    print()
    if all_passed:
        print("  ALL TESTS PASSED — safe to push to GitHub")
    else:
        print("  SOME TESTS FAILED — review errors above before deploying")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
