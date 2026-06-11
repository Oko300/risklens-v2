"""
core/delta.py — RiskLens v2
=============================
Sentence-level diff engine for Risk Factors and MD&A sections.

Algorithm
---------
1. Split both older and newer text into sentences using a punctuation-aware
   regex that avoids splitting on common abbreviations.
2. Sentence sets are capped at MAX_SENTENCES to bound O(n*m) difflib cost.
3. Python's SequenceMatcher is used to align the two lists.
4. Each opcode block is classified as: unchanged, rewritten, added, removed.
5. Within "replace" blocks a Jaccard-similarity scorer pairs sentences — pairs
   above SIM_REWRITE_THRESHOLD are labelled "rewritten" (not add+remove).
6. The percentage of changed sentences determines ChangeMagnitude.
7. Both sections are diffed in parallel threads for speed on large 10-K filings.
"""

import re
import concurrent.futures
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional


class ChangeMagnitude(str, Enum):
    NONE     = "none"
    MINOR    = "minor"
    MODERATE = "moderate"
    MAJOR    = "major"


@dataclass
class SentenceChange:
    change_type: str           # "unchanged" | "added" | "removed" | "rewritten"
    older_text:  Optional[str]
    newer_text:  Optional[str]
    similarity:  float = 1.0


@dataclass
class SectionDelta:
    section_name:           str
    magnitude:              ChangeMagnitude
    total_older_sentences:  int
    total_newer_sentences:  int
    added_count:            int
    removed_count:          int
    rewritten_count:        int
    unchanged_count:        int
    changes:                list[SentenceChange]
    pct_changed:            float
    delta_success:          bool = True
    failure_reason:         Optional[str] = None


@dataclass
class DeltaResult:
    risk_factors:    SectionDelta
    mda:             SectionDelta
    comparison_note: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SENTENCES         = 600   # cap to prevent O(n*m) blowup on large 10-K filings
SIM_REWRITE_THRESHOLD = 0.40  # Jaccard similarity above which a pair is "rewritten"
_MIN_SENT_LEN         = 20
_SENT_SPLIT           = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'(])')


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_delta(
    older_risk: Optional[str],
    newer_risk: Optional[str],
    older_mda:  Optional[str],
    newer_mda:  Optional[str],
) -> DeltaResult:
    """
    Compute sentence-level deltas for Risk Factors and MD&A in parallel.

    Args:
        older_risk: Risk Factors text from the older filing.
        newer_risk: Risk Factors text from the newer filing.
        older_mda:  MD&A text from the older filing.
        newer_mda:  MD&A text from the newer filing.

    Returns:
        DeltaResult with a SectionDelta for each section.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        risk_future = executor.submit(_compare_section, "risk_factors", older_risk, newer_risk)
        mda_future  = executor.submit(_compare_section, "mda",          older_mda,  newer_mda)
        risk_delta  = risk_future.result()
        mda_delta   = mda_future.result()

    return DeltaResult(risk_factors=risk_delta, mda=mda_delta)


# ---------------------------------------------------------------------------
# Section comparison
# ---------------------------------------------------------------------------

def _compare_section(
    section_name: str,
    older_text:   Optional[str],
    newer_text:   Optional[str],
) -> SectionDelta:
    if not older_text and not newer_text:
        return _failed_delta(section_name, "Both older and newer text are missing")
    if not older_text:
        return _failed_delta(section_name, "Older filing text missing — cannot compare")
    if not newer_text:
        return _failed_delta(section_name, "Newer filing text missing — cannot compare")

    older_sents = _to_sentences(older_text)[:MAX_SENTENCES]
    newer_sents = _to_sentences(newer_text)[:MAX_SENTENCES]
    changes     = _diff_sentences(older_sents, newer_sents)

    added     = sum(1 for c in changes if c.change_type == "added")
    removed   = sum(1 for c in changes if c.change_type == "removed")
    rewritten = sum(1 for c in changes if c.change_type == "rewritten")
    unchanged = sum(1 for c in changes if c.change_type == "unchanged")

    max_len     = max(len(older_sents), len(newer_sents), 1)
    pct_changed = (added + removed + rewritten) / max_len

    if pct_changed == 0:
        magnitude = ChangeMagnitude.NONE
    elif pct_changed < 0.10:
        magnitude = ChangeMagnitude.MINOR
    elif pct_changed < 0.30:
        magnitude = ChangeMagnitude.MODERATE
    else:
        magnitude = ChangeMagnitude.MAJOR

    return SectionDelta(
        section_name=section_name,
        magnitude=magnitude,
        total_older_sentences=len(older_sents),
        total_newer_sentences=len(newer_sents),
        added_count=added,
        removed_count=removed,
        rewritten_count=rewritten,
        unchanged_count=unchanged,
        changes=changes,
        pct_changed=round(pct_changed, 4),
        delta_success=True,
    )


# ---------------------------------------------------------------------------
# Sentence diffing
# ---------------------------------------------------------------------------

def _diff_sentences(older: list[str], newer: list[str]) -> list[SentenceChange]:
    matcher = SequenceMatcher(isjunk=None, a=older, b=newer, autojunk=False)
    changes: list[SentenceChange] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                changes.append(SentenceChange(
                    change_type="unchanged",
                    older_text=older[i1 + k],
                    newer_text=newer[j1 + k],
                    similarity=1.0,
                ))
        elif tag == "replace":
            changes.extend(_classify_replace_block(older[i1:i2], newer[j1:j2]))
        elif tag == "delete":
            for s in older[i1:i2]:
                changes.append(SentenceChange(
                    change_type="removed", older_text=s, newer_text=None, similarity=0.0,
                ))
        elif tag == "insert":
            for s in newer[j1:j2]:
                changes.append(SentenceChange(
                    change_type="added", older_text=None, newer_text=s, similarity=0.0,
                ))

    return changes


def _classify_replace_block(
    old_block: list[str], new_block: list[str],
) -> list[SentenceChange]:
    changes:  list[SentenceChange] = []
    used_new: set[int]             = set()

    for old_s in old_block:
        best_j   = -1
        best_sim = 0.0

        for j, new_s in enumerate(new_block):
            if j in used_new:
                continue
            sim = _sentence_similarity(old_s, new_s)
            if sim > best_sim:
                best_sim = sim
                best_j   = j
                if sim >= 0.95:   # early exit on near-identical sentences
                    break

        if best_sim >= SIM_REWRITE_THRESHOLD:
            used_new.add(best_j)
            changes.append(SentenceChange(
                change_type="rewritten",
                older_text=old_s,
                newer_text=new_block[best_j],
                similarity=round(best_sim, 3),
            ))
        else:
            changes.append(SentenceChange(
                change_type="removed", older_text=old_s, newer_text=None, similarity=0.0,
            ))

    for j, new_s in enumerate(new_block):
        if j not in used_new:
            changes.append(SentenceChange(
                change_type="added", older_text=None, newer_text=new_s, similarity=0.0,
            ))

    return changes


def _sentence_similarity(a: str, b: str) -> float:
    tokens_a = set(_tokenize(a))
    tokens_b = set(_tokenize(b))
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def _to_sentences(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    raw  = _SENT_SPLIT.split(text)
    return [s.strip() for s in raw if len(s.strip()) >= _MIN_SENT_LEN]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-z]{3,}\b", text.lower())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failed_delta(section_name: str, reason: str) -> SectionDelta:
    return SectionDelta(
        section_name=section_name,
        magnitude=ChangeMagnitude.NONE,
        total_older_sentences=0, total_newer_sentences=0,
        added_count=0, removed_count=0, rewritten_count=0, unchanged_count=0,
        changes=[], pct_changed=0.0,
        delta_success=False, failure_reason=reason,
    )