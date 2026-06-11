# RiskLens v2

**AI-powered SEC filing risk analysis for investors, analysts, and developers.**

RiskLens v2 connects directly to EDGAR and gives you structured, machine-readable
risk intelligence from 10-Q and 10-K filings — in seconds, inside any MCP-compatible
client (Claude, Cursor, Windsurf, VS Code, and more).

---

## What it does

### `compare_filings` — Head-to-head filing comparison
Compares the two most recent 10-Q or 10-K filings for any US public company.

- Fetches live from EDGAR (no stale data)
- Extracts Risk Factors (Item 1A) and MD&A (Item 7/2)
- Runs a sentence-level diff to find exactly what changed
- Scores each section for materiality using a tiered financial signal library
- Flags 10-Q reference pointers (incorporated-by-reference) instead of producing misleading comparisons

**Example prompts:**
> "Compare TSLA's last two 10-K filings"
> "Did Apple add any new risk factors in their latest 10-Q?"
> "What changed in Meta's MD&A this quarter?"

---

### `analyze_risk_trends` — Multi-year risk trajectory
Tracks how a company's risk profile evolves across up to 8 consecutive filings.

- Builds a timeline of materiality scores (one data point per filing)
- Tracks which risk signals appeared or disappeared at each filing date
- Classifies the overall trajectory: improving / deteriorating / stable
- Identifies the peak-risk filing and key turning points
- Works best with `form_type='10-K'` for clean annual comparisons

**Example prompts:**
> "Has NFLX's risk profile been deteriorating over the last 4 annual filings?"
> "When did 'going concern' language first appear in Peloton's filings?"
> "Show me JPM's risk trend across the last 6 quarters"

---

### `categorize_risks` — Domain-by-domain risk breakdown
Categorizes every risk factor in the most recent filing into 10 standardized domains
and generates an executive summary.

**Risk domains:**
1. Financial & Liquidity
2. Legal & Regulatory
3. Cybersecurity & Data
4. Operational
5. Market & Competitive
6. Macroeconomic & Geopolitical
7. Strategic & Execution
8. Technology & Innovation
9. ESG & Climate
10. Reputational & Brand

**Example prompts:**
> "What are MSFT's top risk categories in their latest 10-K?"
> "Categorize Nvidia's risk factors and give me an executive summary"
> "What cybersecurity risks does CrowdStrike disclose?"

---

## Supported filings

| Form type | Supported | Notes |
|-----------|-----------|-------|
| 10-K      | ✅        | Full Risk Factors + MD&A |
| 10-Q      | ✅        | MD&A + Risk Factors (or reference pointer detection) |
| 20-F      | ❌        | Foreign private issuers not supported |
| 8-K       | ❌        | Event filings not supported |

---

## Quick start

Once connected via MCPize, just ask your AI client natural-language questions.
No API keys, no configuration — it works out of the box.
compare_filings(ticker="AAPL", form_type="10-K")
analyze_risk_trends(ticker="NVDA", form_type="10-K", n_filings=4)
categorize_risks(ticker="MSFT", form_type="10-K")

---

## Environment variables (optional)

| Variable       | Purpose                                | Default      |
|----------------|----------------------------------------|--------------|
| `REDIS_HOST`   | Redis hostname for extraction cache    | None (no cache) |
| `REDIS_PORT`   | Redis port                             | 6379         |
| `REDIS_PASSWORD` | Redis password                       | None         |
| `PORT`         | HTTP port for the MCP server           | 8080         |

Redis is optional. Without it, every request re-parses the filing HTML.
With it, extracted sections are cached for 24 hours — recommended for production.

---

## Coverage disclosures

- Only Risk Factors (Item 1A) and MD&A (Item 2 / Item 7) are analyzed.
- Only the most recent N filings are fetched per request.
- Many 10-Q filings incorporate Risk Factors by reference from the annual 10-K.
  When detected, the tool flags this instead of running a misleading comparison.
  Use `form_type='10-K'` for reliable multi-year risk factor analysis.
- Extraction may fail on image-based or heavily structured filings.
- Materiality scores are signal-based estimates, not investment advice.

---

## Self-hosting

```bash
git clone https://github.com/your-org/risklens-v2
cd risklens-v2
pip install -r requirements.txt
cp .env.example .env   # set REDIS_HOST etc.
python server.py
```

The server binds to `0.0.0.0:8080` by default.

---

## License

MIT — see LICENSE file.

---

*RiskLens v2 is a research tool. Nothing it produces constitutes investment,
legal, or financial advice. Always verify against the original EDGAR filings.*