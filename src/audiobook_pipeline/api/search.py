"""Fuzzy scoring and path hint extraction for Audible search results.

Combines rapidfuzz string matching with Audible search results to find
the best metadata match for an audiobook file.
"""

import re
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

log = logger.bind(stage="search")


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
        title_score = fuzz.token_sort_ratio(
            title_hint.lower(), r["title"].lower(),
        ) * 0.6

        if author_hint:
            author_scores = [
                fuzz.partial_ratio(author_hint.lower(), a.lower())
                for a in r["authors"]
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
