"""Fuzzy scoring and path hint extraction for Audible search results.

Combines rapidfuzz string matching with Audible search results to find
the best metadata match for an audiobook file.
"""

import re
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

log = logger.bind(stage="search")


# Audible titles routinely carry an edition/series subtitle after a colon or a
# dash: "Forsworn: A Powder Mage Novella", "Exile - Book Two of the Dark Elf
# Trilogy". The source folder almost never repeats it.
_SUBTITLE_RE = re.compile(r"\s*[:–-]\s+.*$")


def _title_score(title_hint: str, candidate: str) -> float:
    """Fuzzy-match a title, not penalising a subtitle the source omits.

    Scores the full candidate AND its pre-subtitle head, taking the better.
    A plain ratio punishes the extra tokens so hard that the WRONG book wins:
    measured 2026-08-01, hint "Forsworn" scored David Estes's exact-title
    "Forsworn" at 100 and Brian McClellan's "Forsworn: A Powder Mage Novella"
    at 41 -- a 35-point weighted gap the 30% author weight could not close,
    even though the folder said "Brian McClellan".

    Taking the MAX (not replacing the full-title score) keeps an exact full
    match ranked at 100, so a candidate whose complete title matches is never
    beaten by one that only matches up to its colon.
    """
    hint = title_hint.lower().strip()
    full = candidate.lower().strip()
    best = fuzz.token_sort_ratio(hint, full)

    head = _SUBTITLE_RE.sub("", full).strip()
    if head and head != full:
        best = max(best, fuzz.token_sort_ratio(hint, head))
    return best


def score_results(
    results: list[dict],
    title_hint: str,
    author_hint: str,
) -> list[dict]:
    """Score each result using rapidfuzz. Returns results with scores, sorted descending.

    Weights: title 60%, author 30%, position bonus 10%.
    """
    log.debug(f"Scoring {len(results)} results against title={title_hint!r}")

    scored = []
    for idx, r in enumerate(results):
        title_score = _title_score(title_hint, r["title"]) * 0.6

        if author_hint:
            author_scores = [
                fuzz.partial_ratio(author_hint.lower(), a.lower()) for a in r["authors"]
            ]
            author_score = max(author_scores, default=0) * 0.3
        else:
            author_score = 0.0

        position_score = max(10 - (idx * 2), 0)

        total = title_score + author_score + position_score
        scored.append({**r, "score": round(total, 1)})

    scored.sort(key=lambda x: x["score"], reverse=True)

    if scored:
        best = scored[0]
        log.debug(f"Best match: {best['title']!r} score={best['score']:.0f}")

    return scored


def parse_source_path(source_path: str) -> dict:
    """Extract title/author hints from a source file path.

    Returns dict with keys: title_hint, author_hint, query.
    """
    log.debug(f"parse_source_path: source_path={source_path!r}")

    p = Path(source_path)

    basename = p.stem if p.is_file() or p.suffix else p.name
    basename = re.sub(r"\s+-\s+[a-f0-9]{16}$", "", basename)

    parent = p.parent
    parent_name = parent.name if parent != Path("/") else ""
    parent_name = re.sub(r"\s+-\s+[a-f0-9]{16}$", "", parent_name)

    author_hint = ""
    if parent_name and parent_name == basename:
        grandparent = parent.parent
        if grandparent != Path("/") and grandparent != Path("."):
            gp_name = grandparent.name
            gp_name = re.sub(r"\s+-\s+[a-f0-9]{16}$", "", gp_name)
            if gp_name:
                parent_name = gp_name

    title_hint = _strip_series_numbers(basename)
    title_hint = re.sub(r"[\[\](){}]", "", title_hint)
    title_hint = re.sub(r"\s+", " ", title_hint).strip()

    if parent_name and parent_name != basename:
        author_hint = parent_name
        author_hint_clean = _strip_series_numbers(author_hint)
        author_hint_clean = re.sub(r"[\[\](){}]", "", author_hint_clean)
        author_hint = re.sub(r"\s+", " ", author_hint_clean).strip()

    result = {
        "title_hint": title_hint,
        "author_hint": author_hint,
        "query": f"{author_hint} {title_hint}".strip() if author_hint else title_hint,
    }
    log.debug(f"parse_source_path: result={result}")
    return result


def _strip_series_numbers(s: str) -> str:
    """Strip series numbering patterns from a string."""
    original = s
    s = re.sub(r"\[[0-9]+\]", "", s)
    s = re.sub(r"#[0-9]+-", "", s)
    s = re.sub(r"^[0-9]+\s*[-\u2013]?\s*", "", s)
    s = re.sub(r"\s[0-9]{1,3}\s", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    if s != original:
        log.debug(f"_strip_series_numbers: {original!r} -> {s!r}")

    return s
