"""
core/extractor.py — RiskLens v2
================================
Extracts Risk Factors and MD&A sections from SEC 10-Q / 10-K HTML filings.

Extraction strategy waterfall (highest confidence first):
  1. anchor_href     — named anchor or id matching Item label
  2. heading_tag     — semantic heading elements (h1–h4, b, strong)
  3. ixbrl_div       — iXBRL bold div/span (modern EDGAR inline XBRL)
  4. toc_link        — table-of-contents hyperlink traversal
  5. pattern_match   — plain-text regex fallback
  6. raw_fallback    — full document (extraction_success = False)

Key behaviours:
  - 10-Q MD&A = Item 2 / 10-K MD&A = Item 7 (form_type aware)
  - TOC entry detection: short matches are skipped; real section is found downstream
  - Cross-reference guard: "See Item 3" in prose does not stop collection
  - 10-K MD&A start patterns require "management" or "discussion" in heading
  - 10-Q Risk Factors reference pointer detection (incorporated-by-reference)
  - Redis cache for extracted sections (TTL = 24 h)
  - pattern_min_chars per spec rejects fragments below section threshold
"""

import json
import os
import re
import unicodedata
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import redis.asyncio as aioredis
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

MIN_SECTION_CHARS     = 500
REDIS_TTL_EXTRACTION  = 86_400   # 24 hours

_REFERENCE_POINTER_THRESHOLD = 2_000
_REFERENCE_POINTER_PHRASES   = [
    "incorporated by reference",
    "annual report on form 10-k",
    "see our 10-k",
    "see part i, item 1a",
    "refer to our annual report",
]


class ExtractionMethod(str, Enum):
    HEADING_TAG       = "heading_tag"
    IXBRL_DIV         = "ixbrl_div"
    PATTERN_MATCH     = "pattern_match"
    ANCHOR_HREF       = "anchor_href"
    TABLE_OF_CONTENTS = "toc_link"
    RAW_FALLBACK      = "raw_fallback"
    FAILED            = "failed"


@dataclass
class SectionResult:
    section_name: str
    item_label: str
    text: Optional[str]
    method: ExtractionMethod
    extraction_success: bool
    failure_reason: Optional[str] = None
    char_count: int = 0
    confidence_score: float = 0.0
    coverage_gap_note: Optional[str] = None
    source_reference: Optional[str] = None   # provenance: filing URL + item label for deep-linking

    def __post_init__(self):
        if self.text:
            self.char_count = len(self.text)


@dataclass
class ExtractionResult:
    filing_accession: str
    filing_date: str
    form_type: str
    risk_factors: SectionResult
    mda: SectionResult
    full_doc_char_count: int = 0
    known_gaps: list[str] = field(default_factory=list)

    @property
    def both_succeeded(self) -> bool:
        return self.risk_factors.extraction_success and self.mda.extraction_success

    @property
    def any_succeeded(self) -> bool:
        return self.risk_factors.extraction_success or self.mda.extraction_success


# ---------------------------------------------------------------------------
# Redis client — shared, optional, never fatal
# ---------------------------------------------------------------------------

_redis_client: Optional[aioredis.Redis] = None


async def _get_redis() -> Optional[aioredis.Redis]:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    host     = os.getenv("REDIS_HOST")
    port     = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD")
    if not host:
        return None
    try:
        _redis_client = aioredis.Redis(
            host=host, port=port, password=password,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        await _redis_client.ping()
    except Exception:
        _redis_client = None
    return _redis_client


async def _cache_get(key: str) -> Optional[str]:
    try:
        r = await _get_redis()
        if r is None:
            return None
        return await r.get(key)
    except Exception:
        return None


async def _cache_set(key: str, value: str, ttl: int) -> None:
    try:
        r = await _get_redis()
        if r is None:
            return
        await r.setex(key, ttl, value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _section_to_dict(s: SectionResult) -> dict:
    return {
        "section_name":       s.section_name,
        "item_label":         s.item_label,
        "text":               s.text,
        "method":             s.method.value,
        "extraction_success": s.extraction_success,
        "failure_reason":     s.failure_reason,
        "char_count":         s.char_count,
        "confidence_score":   s.confidence_score,
        "coverage_gap_note":  s.coverage_gap_note,
        "source_reference":   s.source_reference,
    }


def _section_from_dict(d: dict) -> SectionResult:
    return SectionResult(
        section_name=d["section_name"],
        item_label=d["item_label"],
        text=d["text"],
        method=ExtractionMethod(d["method"]),
        extraction_success=d["extraction_success"],
        failure_reason=d["failure_reason"],
        char_count=d["char_count"],
        confidence_score=d["confidence_score"],
        coverage_gap_note=d["coverage_gap_note"],
        source_reference=d.get("source_reference"),
    )


def _result_to_json(result: ExtractionResult) -> str:
    return json.dumps({
        "filing_accession":    result.filing_accession,
        "filing_date":         result.filing_date,
        "form_type":           result.form_type,
        "risk_factors":        _section_to_dict(result.risk_factors),
        "mda":                 _section_to_dict(result.mda),
        "full_doc_char_count": result.full_doc_char_count,
        "known_gaps":          result.known_gaps,
    })


def _result_from_json(raw: str) -> ExtractionResult:
    d = json.loads(raw)
    return ExtractionResult(
        filing_accession=d["filing_accession"],
        filing_date=d["filing_date"],
        form_type=d["form_type"],
        risk_factors=_section_from_dict(d["risk_factors"]),
        mda=_section_from_dict(d["mda"]),
        full_doc_char_count=d["full_doc_char_count"],
        known_gaps=d["known_gaps"],
    )


# ---------------------------------------------------------------------------
# Section specs — form-type aware
# ---------------------------------------------------------------------------

def _build_specs(form_type: str) -> dict:
    # 20-F (foreign private issuers, e.g. TSMC, Alibaba, ASML): Risk Factors
    # live under Item 3.D, and the MD&A equivalent is Item 5 "Operating and
    # Financial Review and Prospects" instead of Item 1A / Item 7.
    if form_type == "20-F":
        risk_spec = {
            "item_label": "Item 3.D",
            "display":    "Risk Factors",
            "start_patterns": [
                r"item\s+3\.?\s*d[\.\s\u2014\-\u2013]+risk\s+factors",
                r"item\s+3\.?\s*d\b",
                r"d\.\s+risk\s+factors",
            ],
            "end_patterns": [
                r"item\s+4\b",
                r"item\s+3\.?\s*e\b",
            ],
            "next_items": ["item 4", "item 3.e"],
            "keywords": [
                "risk", "could", "may", "uncertain", "adverse", "material",
                "competition", "regulatory", "harm", "impact", "exposure",
                "liability", "loss", "failure", "breach",
            ],
            "pattern_min_chars": 1_500,
        }
        mda_spec = {
            "item_label": "Item 5",
            "display":    "Operating and Financial Review and Prospects",
            "start_patterns": [
                r"item\s+5[\.\s\u2014\-\u2013]+operating\s+and\s+financial",
                r"item\s+5[\.\s]+operating",
                r"item\s+5\b",
            ],
            "end_patterns": [
                r"item\s+6\b",
            ],
            "next_items": ["item 6"],
            "keywords": [
                "revenue", "operating", "liquidity", "results", "cash",
                "quarter", "year", "income", "loss", "expense", "financial",
                "increased", "decreased", "compared", "net",
            ],
            "pattern_min_chars": 4_000,
        }
        return {"risk_factors": risk_spec, "mda": mda_spec}

    risk_spec = {
        "item_label": "Item 1A",
        "display":    "Risk Factors",
        "start_patterns": [
            r"item\s+1a[\.\s\u2014\-\u2013]+risk\s+factors",
            r"item\s+1a\b",
        ],
        "end_patterns": [
            r"item\s+1b\b",
            r"item\s+2\b",
        ],
        "next_items": ["item 1b", "item 2"],
        "keywords": [
            "risk", "could", "may", "uncertain", "adverse", "material",
            "competition", "regulatory", "harm", "impact", "exposure",
            "liability", "loss", "failure", "breach",
        ],
        "pattern_min_chars": 1_500,
    }

    if form_type == "10-Q":
        mda_spec = {
            "item_label": "Item 2",
            "display":    "Management's Discussion and Analysis",
            "start_patterns": [
                r"item\s+2[\.\s\u2014\-\u2013]+management",
                r"item\s+2[\.\s]+discussion",
                r"item\s+2[\.\s]+md",
            ],
            "end_patterns": [
                r"item\s+3\b",
                r"item\s+3[\.\s]+quantitative",
                r"item\s+4\b",
            ],
            "next_items": ["item 3", "item 4"],
            "keywords": [
                "revenue", "operating", "liquidity", "results", "cash",
                "quarter", "year", "income", "loss", "expense", "financial",
                "increased", "decreased", "compared", "net",
            ],
            "pattern_min_chars": 4_000,
        }
    else:
        mda_spec = {
            "item_label": "Item 7",
            "display":    "Management's Discussion and Analysis",
            "start_patterns": [
                r"item\s+7[\.\s\u2014\-\u2013]+management",
                r"item\s+7[\.\s]+discussion",
                r"item\s+7[\.\s]+md",
                r"item\s+7\b",
            ],
            "end_patterns": [
                r"item\s+7a\b",
                r"item\s+8\b",
                r"item\s+8[\.\s]+financial",
            ],
            "next_items": ["item 7a", "item 8"],
            "keywords": [
                "revenue", "operating", "liquidity", "results", "cash",
                "quarter", "year", "income", "loss", "expense", "financial",
                "increased", "decreased", "compared", "net",
            ],
            "pattern_min_chars": 4_000,
        }

    return {"risk_factors": risk_spec, "mda": mda_spec}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def extract_sections_cached(
    html: str,
    accession: str = "",
    filing_date: str = "",
    form_type: str = "10-Q",
    document_url: str = "",
) -> ExtractionResult:
    """
    Async extract with Redis caching.
    Cache key: edgar:extraction:{accession}:{form_type}
    """
    cache_key = f"edgar:extraction:{accession}:{form_type}"

    cached = await _cache_get(cache_key)
    if cached:
        try:
            return _result_from_json(cached)
        except Exception:
            pass

    result = extract_sections(html, accession, filing_date, form_type, document_url)

    try:
        await _cache_set(cache_key, _result_to_json(result), REDIS_TTL_EXTRACTION)
    except Exception:
        pass

    return result


def extract_sections(
    html: str,
    accession: str = "",
    filing_date: str = "",
    form_type: str = "10-Q",
    document_url: str = "",
) -> ExtractionResult:
    soup            = BeautifulSoup(html, "lxml")
    _strip_boilerplate(soup)
    full_text       = soup.get_text(separator="\n", strip=True)
    full_char_count = len(full_text)
    specs           = _build_specs(form_type)

    risk_result = _extract_section(soup, full_text, "risk_factors", specs, accession, form_type)
    mda_result  = _extract_section(soup, full_text, "mda",          specs, accession, form_type)

    # Provenance: attach a deep-linkable source reference to each section
    if document_url:
        risk_result.source_reference = f"{document_url}#{specs['risk_factors']['item_label'].replace(' ', '')}"
        mda_result.source_reference  = f"{document_url}#{specs['mda']['item_label'].replace(' ', '')}"

    gaps        = _identify_gaps(risk_result, mda_result, full_char_count)

    return ExtractionResult(
        filing_accession=accession,
        filing_date=filing_date,
        form_type=form_type,
        risk_factors=risk_result,
        mda=mda_result,
        full_doc_char_count=full_char_count,
        known_gaps=gaps,
    )


# ---------------------------------------------------------------------------
# Per-section extraction
# ---------------------------------------------------------------------------

def _extract_section(
    soup: BeautifulSoup, full_text: str,
    section_key: str, specs: dict, accession: str,
    form_type: str = "10-Q",
) -> SectionResult:
    spec = specs[section_key]

    for strategy_fn, method, confidence in [
        (_try_anchor_strategy,  ExtractionMethod.ANCHOR_HREF,       0.92),
        (_try_heading_strategy, ExtractionMethod.HEADING_TAG,        0.85),
        (_try_ixbrl_div_strategy, ExtractionMethod.IXBRL_DIV,        0.82),
        (_try_toc_strategy,     ExtractionMethod.TABLE_OF_CONTENTS,  0.78),
    ]:
        text = strategy_fn(soup, spec)
        if text and _plausible(text, spec):
            return _check_reference_pointer(
                section_key, spec, text, method, confidence, form_type
            )

    text = _try_pattern_strategy(full_text, spec)
    if text and _plausible(text, spec):
        return _check_reference_pointer(
            section_key, spec, text, ExtractionMethod.PATTERN_MATCH, 0.70, form_type
        )

    gap = (
        f"{spec['display']} ({spec['item_label']}) could not be isolated "
        f"from accession {accession}. Returning full document text as fallback."
    )
    return SectionResult(
        section_name=section_key,
        item_label=spec["item_label"],
        text=full_text,
        method=ExtractionMethod.RAW_FALLBACK,
        extraction_success=False,
        failure_reason=f"All extraction strategies failed for {spec['display']}",
        confidence_score=0.20,
        coverage_gap_note=gap,
    )


# ---------------------------------------------------------------------------
# Reference pointer detection
# ---------------------------------------------------------------------------

def _is_reference_pointer(text: str) -> bool:
    if len(text) >= _REFERENCE_POINTER_THRESHOLD:
        return False
    return any(phrase in text.lower() for phrase in _REFERENCE_POINTER_PHRASES)


def _check_reference_pointer(
    section_key: str, spec: dict, text: str,
    method: ExtractionMethod, confidence: float,
    form_type: str,
) -> SectionResult:
    if section_key == "risk_factors" and form_type == "10-Q" and _is_reference_pointer(text):
        return SectionResult(
            section_name=section_key,
            item_label=spec["item_label"],
            text=text,
            method=method,
            extraction_success=False,
            failure_reason=(
                "Risk Factors section in this 10-Q incorporates by reference "
                "from the annual 10-K filing."
            ),
            confidence_score=0.0,
            coverage_gap_note=(
                "Risk Factors section in this 10-Q references the 10-K; "
                "no material Q-over-Q comparison possible. "
                "Use the annual 10-K filings to compare Risk Factors year-over-year."
            ),
        )
    return _ok(section_key, spec, text, method, confidence)


# ---------------------------------------------------------------------------
# Strategy 1: anchor / id
# ---------------------------------------------------------------------------

def _try_anchor_strategy(soup: BeautifulSoup, spec: dict) -> Optional[str]:
    item_norm = spec["item_label"].lower().replace(" ", "")
    for tag in soup.find_all(True, attrs=True):
        for attr in ("name", "id"):
            val = re.sub(r"[\s\-_]", "", (tag.get(attr) or "").lower())
            if item_norm in val:
                text = _collect_forward(tag, spec["next_items"])
                if text and len(text) >= MIN_SECTION_CHARS:
                    return text
                break
    return None


# ---------------------------------------------------------------------------
# Strategy 2: semantic heading tags
# ---------------------------------------------------------------------------

def _try_heading_strategy(soup: BeautifulSoup, spec: dict) -> Optional[str]:
    start_re = re.compile("|".join(spec["start_patterns"]), re.IGNORECASE)
    for selector in ["h1", "h2", "h3", "h4", "b", "strong"]:
        for tag in soup.select(selector):
            if start_re.search(_norm(tag.get_text())) and not _looks_like_toc_entry(tag):
                text = _collect_forward(tag, spec["next_items"])
                if text and len(text) >= MIN_SECTION_CHARS:
                    return text
    return None


# ---------------------------------------------------------------------------
# Strategy 3: iXBRL bold div/span
# ---------------------------------------------------------------------------

_BOLD_STYLE_RE = re.compile(r"font-weight\s*:\s*(bold|700|800|900)", re.IGNORECASE)


def _try_ixbrl_div_strategy(soup: BeautifulSoup, spec: dict) -> Optional[str]:
    start_re  = re.compile("|".join(spec["start_patterns"]), re.IGNORECASE)
    best_text = None

    for tag in soup.find_all(["div", "span", "p", "td"]):
        if not _BOLD_STYLE_RE.search(tag.get("style", "")):
            continue
        if not start_re.search(_norm(tag.get_text())):
            continue
        if _looks_like_toc_entry(tag):
            continue

        text = _collect_forward(tag, spec["next_items"])
        if not text:
            continue
        if len(text) >= MIN_SECTION_CHARS:
            return text
        if best_text is None or len(text) > len(best_text):
            best_text = text

    return best_text


# ---------------------------------------------------------------------------
# Strategy 4: TOC link traversal
# ---------------------------------------------------------------------------

def _try_toc_strategy(soup: BeautifulSoup, spec: dict) -> Optional[str]:
    start_re  = re.compile("|".join(spec["start_patterns"]), re.IGNORECASE)
    target_id = None
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("#") and start_re.search(_norm(a.get_text())):
            target_id = a["href"][1:]
            break
    if not target_id:
        return None
    target = soup.find(id=target_id) or soup.find(attrs={"name": target_id})
    if not target:
        return None
    text = _collect_forward(target, spec["next_items"])
    return text if text and len(text) >= MIN_SECTION_CHARS else None


# ---------------------------------------------------------------------------
# Strategy 5: plain text pattern match
# ---------------------------------------------------------------------------

def _try_pattern_strategy(full_text: str, spec: dict) -> Optional[str]:
    start_re    = re.compile("|".join(spec["start_patterns"]), re.IGNORECASE)
    end_re      = re.compile("|".join(spec["end_patterns"]),   re.IGNORECASE)
    best_text   = None
    search_from = 0

    while True:
        m = start_re.search(full_text, search_from)
        if not m:
            break
        end_m = end_re.search(full_text, m.end())
        candidate = (
            full_text[m.start():end_m.start()].strip()
            if end_m
            else full_text[m.start(): m.start() + 80_000].strip()
        )

        if len(candidate) >= spec.get("pattern_min_chars", MIN_SECTION_CHARS):
            if best_text is None or len(candidate) > len(best_text):
                best_text = candidate
            break

        search_from = m.end()

    return best_text


# ---------------------------------------------------------------------------
# Shared forward text collector
# ---------------------------------------------------------------------------

def _collect_forward(
    start_tag,
    end_items: list[str],
    max_chars: int = 150_000,
) -> Optional[str]:
    try:
        all_tags = list(start_tag.find_all_next(True))
    except Exception:
        return None

    chunks = []
    total  = 0

    for tag in all_tags:
        if total >= max_chars:
            break

        tag_text = (
            tag.get_text(separator=" ", strip=True)
            if hasattr(tag, "get_text") else str(tag)
        )
        tag_norm = _norm(tag_text)

        if _looks_like_standalone_heading(tag):
            for end_item in end_items:
                if tag_norm.startswith(end_item) or f"\n{end_item}" in tag_norm:
                    result = "\n".join(chunks).strip()
                    return result if result else None

        snippet = tag_text.strip()
        if snippet:
            chunks.append(snippet)
            total += len(snippet)

    result = "\n".join(chunks).strip()
    return result if result else None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"\s+", " ", text.lower()).strip()


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "ix:header", "ix:hidden"]):
        tag.decompose()


def _plausible(text: str, spec: dict) -> bool:
    if not text or len(text) < 200:
        return False
    text_lower = text.lower()
    hits = sum(1 for kw in spec["keywords"] if kw in text_lower)
    return hits >= 2


def _looks_like_toc_entry(tag) -> bool:
    text = tag.get_text(strip=True)
    return len(text) < 80 and bool(re.search(r"\d{1,3}\s*$", text))


def _looks_like_standalone_heading(tag) -> bool:
    if not hasattr(tag, "name"):
        return False
    if tag.name in ("h1", "h2", "h3", "h4"):
        return True
    if tag.name in ("div", "span"):
        if _BOLD_STYLE_RE.search(tag.get("style", "")):
            text        = tag.get_text(strip=True)
            parent      = tag.parent
            parent_name = getattr(parent, "name", "") if parent else ""
            if len(text) < 150 and parent_name not in ("p", "li", "span"):
                return True
    if tag.name in ("b", "strong"):
        text        = tag.get_text(strip=True)
        parent      = tag.parent
        parent_name = getattr(parent, "name", "") if parent else ""
        if parent_name == "p" and len(parent.get_text(strip=True)) == len(text):
            return True
    return False


def _ok(
    section_key: str, spec: dict, text: str,
    method: ExtractionMethod, confidence: float,
) -> SectionResult:
    return SectionResult(
        section_name=section_key, item_label=spec["item_label"],
        text=text, method=method, extraction_success=True,
        confidence_score=confidence,
    )


def _identify_gaps(
    risk: SectionResult, mda: SectionResult, full_char_count: int,
) -> list[str]:
    gaps = []
    if not risk.extraction_success:
        gaps.append(f"Risk Factors could not be isolated (method: {risk.method.value})")
    if not mda.extraction_success:
        gaps.append(f"MD&A could not be isolated (method: {mda.method.value})")
    if full_char_count < 5_000:
        gaps.append("Filing HTML is unusually short — may be a stub or redirect")
    if full_char_count > 2_000_000:
        gaps.append("Filing HTML is very large (>2MB) — extraction may have boundary errors")
    return gaps