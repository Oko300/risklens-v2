# RiskLens v2

> **AI-powered SEC filing risk intelligence — live from EDGAR, structured for AI clients.**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastMCP](https://img.shields.io/badge/FastMCP-latest-green.svg)](https://github.com/jlowin/fastmcp)
[![EDGAR](https://img.shields.io/badge/Data-SEC%20EDGAR-orange.svg)](https://www.sec.gov/edgar)
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)

RiskLens v2 connects directly to the SEC's EDGAR database and delivers
structured, machine-readable risk intelligence from 10-Q and 10-K filings
in seconds — inside Claude, Cursor, Windsurf, VS Code, or any MCP client.

---

## Real output — AAPL 10-K (tested live)

```
RISKLENS v2 — ANALYST REPORT
AAPL  |  10-K  |  Filed 2025-10-31
════════════════════════════════════════════════════════════════

OVERALL RISK RATING:  🔴 CRITICAL

EXECUTIVE SUMMARY
────────────────────────────────────────
Apple's most recent annual filing (2025-10-31) carries a CRITICAL
risk rating. Risk Factors changed significantly from the prior filing
(2024-11-01), with 80% of sentences affected. Notable new risk signals
include "trade restriction", "DOJ", "insolvency". The MD&A section
reflects stable financial performance.

WHAT CHANGED VS PRIOR FILING
────────────────────────────────────────
Risk Factors:  significant changes (80% of sentences affected)
  ⚠  New risk signals appeared: trade restriction, DOJ, insolvency
MD&A:          significant changes (84% of sentences affected)

TOP RISK SIGNALS
────────────────────────────────────────
Critical (Tier 1):
  • trade restriction [NEW]
  • DOJ [NEW]
  • insolvency [NEW]
  • regulatory action

RISK CATEGORIES PRESENT
────────────────────────────────────────
  Macroeconomic & Geopolitical          5 signals
  Cybersecurity & Data                  4 signals
  Operational                           4 signals
  Legal & Regulatory                    3 signals

SECTION SCORES
────────────────────────────────────────
  Risk Factors:  🔴 CRITICAL  (score 50.9)
  MD&A:          🟠 HIGH      (score 27.4)
```

---

## Four tools

### `generate_executive_report` — Ready-to-share analyst report
The fastest way to understand a company's risk profile.
Produces a clean, formatted report you can paste directly into
an email, Slack message, investment memo, or board deck.

```
generate_executive_report(ticker="NVDA", form_type="10-K")
```

---

### `compare_filings` — Head-to-head filing comparison
Sentence-level diff between the two most recent filings.
Returns structured JSON with materiality scores and signal lists.

```
compare_filings(ticker="AAPL", form_type="10-K")
compare_filings(ticker="TSLA", form_type="10-Q")
```

---

### `analyze_risk_trends` — Multi-year risk trajectory
Track how a company's risk profile evolves across up to 8 filings.
Identifies when specific risks first appeared or disappeared.

```
analyze_risk_trends(ticker="MSFT", form_type="10-K", n_filings=4)
```

---

### `categorize_risks` — Domain-by-domain risk breakdown
Classifies every risk factor into 10 standardized domains.
Returns signal counts, excerpts, and an executive summary.

```
categorize_risks(ticker="JPM", form_type="10-K")
```

---

## Example prompts (works in Claude, Cursor, Windsurf)

```
"Generate an executive report for Tesla's latest 10-K"
"Has Nvidia's risk profile been getting worse over the last 3 years?"
"What cybersecurity risks does Microsoft disclose in their 10-K?"
"Compare Apple's last two quarterly filings"
"Categorize all risk factors in Amazon's latest annual report"
"Did Meta add any new risk signals in their most recent 10-Q?"
```

---

## Architecture

```
risklens-v2/
├── server.py                  # FastMCP server entry point
├── schemas.py                 # All Pydantic output models
├── tools/
│   ├── compare_filings.py     # Tool 1: head-to-head comparison
│   ├── risk_trends.py         # Tool 2: multi-year trend analysis
│   ├── risk_categorizer.py    # Tool 3: domain categorization
│   └── executive_report.py   # Tool 4: formatted analyst report
├── core/
│   ├── fetcher.py             # EDGAR HTTP client (rate-limited, retry)
│   ├── extractor.py           # Section extraction (5-strategy waterfall)
│   ├── delta.py               # Sentence-level diff engine
│   └── scorer.py              # Materiality scoring (tiered signals)
├── auth/
│   └── middleware.py          # Context Protocol JWT auth (ASGI)
└── README.md
```

---

## Extraction engine

Five strategies tried in order, highest confidence first:

| Strategy | Method | Confidence |
|----------|--------|------------|
| Named anchor / id match | `anchor_href` | 0.92 |
| Semantic heading tags (h1–h4) | `heading_tag` | 0.85 |
| iXBRL bold div/span (modern EDGAR) | `ixbrl_div` | 0.82 |
| Table-of-contents link traversal | `toc_link` | 0.78 |
| Plain-text regex pattern match | `pattern_match` | 0.70 |

If all strategies fail the tool returns a **clean error** — it never
silently falls back to dumping the full document into the analysis.

---

## Self-hosting

```bash
git clone https://github.com/your-org/risklens-v2
cd risklens-v2
pip install -r requirements.txt
cp .env.example .env
python server.py
```

**Environment variables:**

| Variable | Purpose | Default |
|----------|---------|---------|
| `UPSTASH_REDIS_REST_URL` | Upstash Redis REST endpoint for tool-result caching | None (caching disabled) |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis REST auth token | None (caching disabled) |
| `CACHE_TTL_DAYS` | Cache lifetime in days (3-7) | 7 |
| `PORT` | Server port | 8080 |

**Setting up caching (recommended for production):**
1. Create a free database at [console.upstash.com](https://console.upstash.com)
2. Copy the REST URL and REST Token from the database details page
3. Set both as environment variables on your hosting platform (Render, etc.)
4. Without these set, every tool still works — it just runs uncached, so
   repeat queries re-fetch from EDGAR each time instead of returning instantly.

Caching applies to `generate_executive_report`, `compare_filings`,
`analyze_8k_events`, and `categorize_risks` — the four most frequently
called tools — checked before any EDGAR fetch so a cache hit returns
in milliseconds rather than seconds.

---

## Disclaimers

- Analyzes 10-Q and 10-K filings only (no 20-F, 8-K, or exhibits)
- Only Risk Factors (Item 1A) and MD&A (Item 2/7) are analyzed
- Many 10-Q filings incorporate Risk Factors by reference from the 10-K —
  use `form_type='10-K'` for reliable risk factor comparisons
- Materiality scores are signal-based estimates, not investment advice
- Always verify findings against the original EDGAR filing

---

## Built with

- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [SEC EDGAR](https://www.sec.gov/edgar) — live filing data
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) — HTML parsing
- [httpx](https://www.python-httpx.org/) — async HTTP client
- [Redis](https://redis.io/) — extraction cache

---

*RiskLens v2 is a research tool. Nothing it produces constitutes
investment, legal, or financial advice. Always verify against the
original EDGAR filings at sec.gov.*
