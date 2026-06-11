"""
test_offline.py — Offline unit tests for RiskLens v2 core logic
No network required. Uses realistic mock SEC filing HTML.
Run this here and locally to confirm all logic works.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.extractor import extract_sections
from core.delta     import compute_delta, ChangeMagnitude
from core.scorer    import score_sections, MaterialityLevel

SEP = "─" * 60

# ---------------------------------------------------------------------------
# Mock 10-K HTML — realistic iXBRL structure with both sections
# ---------------------------------------------------------------------------

MOCK_10K_HTML = """
<html><body>
<div>
  <a href="#item1a">Item 1A. Risk Factors</a>
  <a href="#item7">Item 7. Management Discussion</a>
</div>

<div id="item1a">
  <div style="font-weight:bold">Item 1A. Risk Factors</div>
  <p>We are subject to a number of significant risks that could adversely affect our business,
  financial condition and results of operations. These risks include competition from other
  companies in our market, regulatory changes that may harm our ability to operate, and
  cybersecurity threats that could result in a data breach of customer information.</p>
  <p>Our operations could be materially affected by macroeconomic conditions including
  inflation and interest rate changes. We face litigation risk from ongoing class action
  lawsuits. Supply chain disruption remains a material uncertainty for our manufacturing
  operations. We may experience declining revenue if customers reduce spending.</p>
  <p>We face risk from foreign exchange and currency risk in international markets.
  Any material weakness in our internal controls could expose us to regulatory action.
  Restructuring activities and workforce reduction may impact employee morale and execution risk.
  Tariff and trade restriction changes could adversely impact our gross margin.
  We may need to pursue refinancing of existing debt facilities before maturity.
  Customer concentration in a few key accounts creates revenue risk if any account is lost.</p>
</div>

<div id="item7">
  <div style="font-weight:bold">Item 7. Management's Discussion and Analysis of Financial Condition</div>
  <p>Revenue for the fiscal year was $394.3 billion, representing an increase compared to
  the prior year period. Operating income increased year-over-year driven by higher gross margins
  and disciplined expense management across all segments.</p>
  <p>Our liquidity position remains strong with $165 billion in cash and investments.
  Net income for the period was $97 billion. We generated strong positive cash flow from
  operations of $110 billion during the fiscal year.</p>
  <p>Results of operations for the year ended September 2024 showed revenue growth in
  Services partially offset by decreased iPhone revenue compared to the prior year.
  We expect continued financial performance improvements driven by innovation and expansion
  into new markets. Capital expenditure for the year totaled $11 billion.</p>
</div>

<div id="item7a">
  <div style="font-weight:bold">Item 7A. Quantitative and Qualitative Disclosures</div>
  <p>Market risk information follows.</p>
</div>
</body></html>
"""

MOCK_10K_HTML_OLDER = """
<html><body>
<div id="item1a">
  <div style="font-weight:bold">Item 1A. Risk Factors</div>
  <p>We are subject to a number of significant risks that could adversely affect our business,
  financial condition and results of operations. These risks include competition from other
  companies in our market, regulatory changes that may harm our ability to operate.</p>
  <p>Our operations could be materially affected by macroeconomic conditions including
  interest rate changes. We face litigation risk from ongoing lawsuits. Supply chain disruption
  remains a material uncertainty for our manufacturing operations.</p>
  <p>We face risk from foreign exchange and currency risk in international markets.
  Restructuring activities may impact employee morale. Tariff and trade restriction changes
  could adversely impact our gross margin. Customer concentration in a few key accounts
  creates revenue risk if any account is lost.</p>
</div>

<div id="item7">
  <div style="font-weight:bold">Item 7. Management's Discussion and Analysis of Financial Condition</div>
  <p>Revenue for the fiscal year was $383.3 billion, representing a decrease compared to
  the prior year period. Operating income decreased year-over-year driven by lower gross margins
  and higher expenses across most segments.</p>
  <p>Our liquidity position remains adequate with $160 billion in cash and investments.
  Net income for the period was $96 billion. We generated positive cash flow from
  operations of $105 billion during the fiscal year.</p>
  <p>Results of operations for the year ended September 2023 showed revenue decline in
  iPhone partially offset by increased Services revenue compared to the prior year.
  Capital expenditure for the year totaled $10 billion.</p>
</div>

<div id="item7a">
  <div style="font-weight:bold">Item 7A. Quantitative and Qualitative Disclosures</div>
  <p>Market risk information follows.</p>
</div>
</body></html>
"""


def print_result(label, passed, detail=""):
    icon = "✓" if passed else "✗"
    line = f"  {icon}  {label}"
    if detail: line += f"  →  {detail}"
    print(line)
    return passed


# ---------------------------------------------------------------------------
# Test 1: Extraction
# ---------------------------------------------------------------------------

def test_extraction():
    print(f"\n{SEP}")
    print("TEST 1: Section Extraction (10-K)")
    print(SEP)

    result = extract_sections(MOCK_10K_HTML, accession="0000001234", filing_date="2024-11-01", form_type="10-K")
    rf  = result.risk_factors
    mda = result.mda

    passed = True
    passed &= print_result("both_succeeded", result.both_succeeded, str(result.both_succeeded))
    passed &= print_result("risk_factors extracted", rf.extraction_success,
                           f"method={rf.method.value}  chars={rf.char_count}  confidence={rf.confidence_score}")
    passed &= print_result("mda extracted", mda.extraction_success,
                           f"method={mda.method.value}  chars={mda.char_count}  confidence={mda.confidence_score}")
    passed &= print_result("risk_factors has content", rf.char_count > 200,
                           f"{rf.char_count} chars")
    passed &= print_result("mda has content", mda.char_count > 200,
                           f"{mda.char_count} chars")
    passed &= print_result("risk_factors item_label", rf.item_label == "Item 1A",
                           rf.item_label)
    passed &= print_result("mda item_label (10-K = Item 7)", mda.item_label == "Item 7",
                           mda.item_label)
    passed &= print_result("no extraction gaps", not any("could not be isolated" in g for g in result.known_gaps),
                           str(result.known_gaps))

    return passed, result


# ---------------------------------------------------------------------------
# Test 2: Delta
# ---------------------------------------------------------------------------

def test_delta(newer_ext, older_ext):
    print(f"\n{SEP}")
    print("TEST 2: Delta Computation")
    print(SEP)

    delta = compute_delta(
        older_risk=older_ext.risk_factors.text,
        newer_risk=newer_ext.risk_factors.text,
        older_mda=older_ext.mda.text,
        newer_mda=newer_ext.mda.text,
    )

    rf  = delta.risk_factors
    mda = delta.mda
    passed = True

    passed &= print_result("risk delta success",  rf.delta_success,  str(rf.delta_success))
    passed &= print_result("mda delta success",   mda.delta_success, str(mda.delta_success))
    passed &= print_result("risk sentences counted",
                           rf.total_older_sentences > 0 and rf.total_newer_sentences > 0,
                           f"older={rf.total_older_sentences}  newer={rf.total_newer_sentences}")
    passed &= print_result("mda sentences counted",
                           mda.total_older_sentences > 0 and mda.total_newer_sentences > 0,
                           f"older={mda.total_older_sentences}  newer={mda.total_newer_sentences}")
    passed &= print_result("risk pct_changed >= 0", rf.pct_changed >= 0,
                           f"{rf.pct_changed*100:.1f}%")
    passed &= print_result("mda has changes detected", mda.added_count + mda.removed_count + mda.rewritten_count > 0,
                           f"added={mda.added_count}  removed={mda.removed_count}  rewritten={mda.rewritten_count}")
    passed &= print_result("risk magnitude valid",
                           rf.magnitude in list(ChangeMagnitude), rf.magnitude.value)
    passed &= print_result("mda magnitude valid",
                           mda.magnitude in list(ChangeMagnitude), mda.magnitude.value)

    print(f"\n  Risk delta:  magnitude={rf.magnitude.value}  "
          f"added={rf.added_count}  removed={rf.removed_count}  rewritten={rf.rewritten_count}")
    print(f"  MDA  delta:  magnitude={mda.magnitude.value}  "
          f"added={mda.added_count}  removed={mda.removed_count}  rewritten={mda.rewritten_count}")

    return passed, delta


# ---------------------------------------------------------------------------
# Test 3: Scorer
# ---------------------------------------------------------------------------

def test_scorer(newer_ext, older_ext, delta):
    print(f"\n{SEP}")
    print("TEST 3: Materiality Scorer")
    print(SEP)

    scoring = score_sections(
        newer_risk_text=newer_ext.risk_factors.text,
        older_risk_text=older_ext.risk_factors.text,
        newer_mda_text=newer_ext.mda.text,
        older_mda_text=older_ext.mda.text,
        risk_delta=delta.risk_factors,
        mda_delta=delta.mda,
    )

    passed = True
    passed &= print_result("scoring_success", scoring.scoring_success, str(scoring.scoring_success))
    passed &= print_result("overall_materiality valid",
                           scoring.overall_materiality in list(MaterialityLevel),
                           scoring.overall_materiality.value.upper())
    passed &= print_result("risk tier1 hits found",
                           len(scoring.risk_factors.tier1_hits) > 0,
                           f"{len(scoring.risk_factors.tier1_hits)} signals: "
                           + ", ".join(h.signal for h in scoring.risk_factors.tier1_hits[:4]))
    passed &= print_result("risk new_signals detected",
                           len(scoring.risk_factors.new_signals) > 0,
                           f"{len(scoring.risk_factors.new_signals)} new: "
                           + ", ".join(h.signal for h in scoring.risk_factors.new_signals[:4]))
    passed &= print_result("analyst_note non-empty",
                           bool(scoring.risk_factors.analyst_note),
                           scoring.risk_factors.analyst_note[:80])
    passed &= print_result("top_signals returned", len(scoring.top_signals) > 0,
                           ", ".join(scoring.top_signals[:5]))

    print(f"\n  Risk score:  {scoring.risk_factors.raw_score:.2f}  "
          f"→  {scoring.risk_factors.materiality.value.upper()}")
    print(f"  MDA  score:  {scoring.mda.raw_score:.2f}  "
          f"→  {scoring.mda.materiality.value.upper()}")
    print(f"  Overall:     {scoring.overall_materiality.value.upper()}")

    return passed, scoring


# ---------------------------------------------------------------------------
# Test 4: Categorizer logic
# ---------------------------------------------------------------------------

RISK_TAXONOMY = {
    "financial":          ["liquidity","cash flow","debt","net loss","going concern","default","leverage"],
    "legal_regulatory":   ["litigation","class action","SEC","DOJ","regulatory","compliance","settlement"],
    "cybersecurity":      ["cybersecurity","data breach","ransomware","unauthorized access","privacy"],
    "operational":        ["supply chain","disruption","key personnel","restructuring","outage"],
    "market_competitive": ["competition","market share","pricing pressure","customer concentration","declining revenue"],
    "macro_geopolitical": ["recession","inflation","interest rate","tariff","sanctions","geopolitical","macroeconomic"],
    "strategic":          ["acquisition","integration risk","execution risk","merger"],
    "technology":         ["artificial intelligence","cloud","obsolescence","intellectual property","technology risk"],
    "esg_climate":        ["climate change","ESG","carbon","sustainability","environmental"],
    "reputational":       ["reputational","brand","public perception","trust"],
}

def test_categorizer(newer_ext):
    print(f"\n{SEP}")
    print("TEST 4: Risk Categorization")
    print(SEP)

    text_lower = newer_ext.risk_factors.text.lower()
    results = []
    for domain, keywords in RISK_TAXONOMY.items():
        matched = [kw for kw in keywords if kw in text_lower]
        if matched:
            results.append((domain, len(matched), matched))
    results.sort(key=lambda x: -x[1])

    passed = True
    passed &= print_result("categories identified", len(results) > 0,
                           f"{len(results)} / {len(RISK_TAXONOMY)} domains")
    passed &= print_result("financial risk found",
                           any(d == "financial" for d,_,_ in results))
    passed &= print_result("legal/regulatory risk found",
                           any(d == "legal_regulatory" for d,_,_ in results))
    passed &= print_result("macro/geopolitical risk found",
                           any(d == "macro_geopolitical" for d,_,_ in results))
    passed &= print_result("operational risk found",
                           any(d == "operational" for d,_,_ in results))

    total_signals = sum(c for _,c,_ in results)
    print(f"\n  {'Domain':<25}  {'Signals':>7}  Keywords matched")
    print(f"  {'─'*25}  {'─'*7}  {'─'*35}")
    for domain, count, matched in results:
        print(f"  {domain:<25}  {count:>7}  {', '.join(matched[:4])}")
    print(f"\n  Total: {len(results)} domains, {total_signals} signals")

    return passed


# ---------------------------------------------------------------------------
# Test 5: Reference pointer detection
# ---------------------------------------------------------------------------

MOCK_10Q_REFERENCE_HTML = """
<html><body>
<div id="item1a">
  <div style="font-weight:bold">Item 1A. Risk Factors</div>
  <p>The information required by this item is incorporated by reference from
  our Annual Report on Form 10-K for the fiscal year ended September 30, 2023,
  Part I, Item 1A. See Part I, Item 1A of our 10-K for a full discussion of
  risks that could materially affect our business.</p>
</div>
<div id="item2">
  <div style="font-weight:bold">Item 2. Management Discussion and Analysis</div>
  <p>Revenue for the quarter was $89.5 billion compared to $90.1 billion in
  the prior year quarter, representing a slight decrease. Operating income
  increased year-over-year. Net income was $22.9 billion.
  Our liquidity remains strong with cash and investments of $162 billion.
  Cash flow from operations was $28.6 billion for the quarter.
  Results compared favorably to analyst estimates. Financial performance
  reflects continued strength in Services revenue which increased significantly.
  We expect fourth quarter results to reflect normal seasonal patterns.</p>
</div>
<div id="item3">
  <div style="font-weight:bold">Item 3. Quantitative Disclosures</div>
</div>
</body></html>
"""

def test_reference_pointer():
    print(f"\n{SEP}")
    print("TEST 5: 10-Q Reference Pointer Detection")
    print(SEP)

    result = extract_sections(MOCK_10Q_REFERENCE_HTML, form_type="10-Q")
    rf  = result.risk_factors
    mda = result.mda

    passed = True
    passed &= print_result("risk_factors flagged as reference pointer",
                           not rf.extraction_success and rf.coverage_gap_note is not None,
                           f"extraction_success={rf.extraction_success}")
    passed &= print_result("risk_factors failure_reason set",
                           bool(rf.failure_reason),
                           (rf.failure_reason or "")[:80])
    passed &= print_result("mda extracted successfully",
                           mda.extraction_success,
                           f"method={mda.method.value}  chars={mda.char_count}")
    passed &= print_result("mda item_label (10-Q = Item 2)",
                           mda.item_label == "Item 2", mda.item_label)

    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  RiskLens v2 — Offline Unit Tests")
    print("  (No network required)")
    print("=" * 60)

    all_passed = True

    t1_passed, newer_ext = test_extraction()
    older_ext = extract_sections(MOCK_10K_HTML_OLDER, accession="0000001233",
                                  filing_date="2023-11-01", form_type="10-K")
    all_passed &= t1_passed

    t2_passed, delta = test_delta(newer_ext, older_ext)
    all_passed &= t2_passed

    t3_passed, scoring = test_scorer(newer_ext, older_ext, delta)
    all_passed &= t3_passed

    t4_passed = test_categorizer(newer_ext)
    all_passed &= t4_passed

    t5_passed = test_reference_pointer()
    all_passed &= t5_passed

    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print("=" * 60)
    for name, passed in [
        ("Extraction",               t1_passed),
        ("Delta computation",        t2_passed),
        ("Materiality scorer",       t3_passed),
        ("Risk categorization",      t4_passed),
        ("Reference pointer detect", t5_passed),
    ]:
        icon = "✓" if passed else "✗"
        print(f"  {icon}  {name}")

    print()
    if all_passed:
        print("  ALL TESTS PASSED ✓")
        print("  Core logic is correct. Run test_risklens.py locally")
        print("  against real EDGAR data to confirm end-to-end, then")
        print("  push to GitHub and deploy on MCPize.")
    else:
        print("  SOME TESTS FAILED — fix issues before deploying")
    print("=" * 60)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
