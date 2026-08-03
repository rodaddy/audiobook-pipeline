"""Finding out which book this actually is, from Audible and Audnexus.

Purpose:
    Everything downstream needs a real title, author, series and ASIN. The
    filename is a hint, not an answer: it says "Powder Mage 0.1 - Forsworn" and
    the catalogue says "Forsworn: A Powder Mage Novella" by Brian McClellan,
    ASIN B00K23Y51K (verified against the live API 2026-08-02).

WHY A SUBTITLE MUST NOT SINK THE RIGHT MATCH
    Audible titles routinely carry an edition subtitle the source folder never
    repeats. Scoring the full string alone punishes those extra tokens so hard
    that the WRONG book wins: measured 2026-08-01, the hint "Forsworn" scored
    David Estes's exact-title "Forsworn" at 100 and Brian McClellan's
    "Forsworn: A Powder Mage Novella" at 41 -- a 35-point weighted gap the 30%
    author weight could not close, even with the folder saying "Brian
    McClellan". So each candidate is scored BOTH ways and keeps the better.

WHY FETCHED CHAPTERS ARE CHECKED AGAINST THE LOCAL DURATION
    Audnexus returns chapters for an ASIN, not for the file on disk. An
    abridged edition, a different narrator's recording, or simply the wrong
    match produces a chapter table whose marks drift further out of place the
    further into the book you get -- and it LOOKS right, because the chapter
    count and the names are plausible. Both a relative and an absolute bound
    must hold before those marks are trusted.

Example:
    >>> _title_score("Forsworn", "Forsworn: A Powder Mage Novella") > 90
    True

See Also:
    - audiobook_pipeline.utils.http: the retry policy these calls run under
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from loguru import logger
from rapidfuzz import fuzz

from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.utils.http import get_json

log = logger.bind(stage="identify")

AUDNEXUS_BASE = "https://api.audnex.us"

#: An edition or series subtitle after a colon or dash: "Forsworn: A Powder
#: Mage Novella", "Exile - Book Two of the Dark Elf Trilogy".
# \u2013 is an EN DASH, written as an escape rather than literally: it is
# visually identical to the ASCII hyphen beside it, and Audible really does
# use both ("Exile \u2013 Book Two" and "Exile - Book Two").
_SUBTITLE = re.compile(r"\s*[:\u2013-]\s+.*$")

#: Relative and absolute bounds, BOTH of which must hold before fetched
#: chapters are accepted. Percentage alone lets a 20-hour book drift ten
#: minutes and still pass; seconds alone would reject a short book over a
#: trivial difference.
DURATION_TOLERANCE_PCT = 2.0
DURATION_TOLERANCE_SEC = 300.0

#: Weights. Title dominates because it is the most specific signal; the author
#: breaks ties between same-titled books; catalogue position is a weak nudge
#: that only matters when the first two are close.
_TITLE_WEIGHT = 0.6
_AUTHOR_WEIGHT = 0.3


def _title_score(title_hint: str, candidate: str) -> float:
    """Score a candidate title, not penalising a subtitle the source omits.

    Args:
        title_hint: The title parsed from the source.
        candidate: The catalogue's title.

    Returns:
        The BETTER of the full-title score and the pre-subtitle score. Taking
        the max rather than replacing keeps an exact full match at 100, so a
        candidate whose complete title matches is never beaten by one that
        matches only up to its colon.
    """
    hint = title_hint.lower().strip()
    full = candidate.lower().strip()
    best = float(fuzz.token_sort_ratio(hint, full))

    head = _SUBTITLE.sub("", full).strip()
    if head and head != full:
        best = max(best, float(fuzz.token_sort_ratio(hint, head)))
    return best


def _author_score(author_hint: str, authors: tuple[str, ...]) -> float:
    """Score a candidate's authors against the hint.

    Args:
        author_hint: The author parsed from the source. May be empty.
        authors: The catalogue's authors.

    Returns:
        The best partial match, or 0.0 when there is no hint to match --
        NOT a neutral 50, which would let an unknown author outrank a known
        mismatch.
    """
    if not author_hint or not authors:
        return 0.0
    return max(
        (float(fuzz.partial_ratio(author_hint.lower(), a.lower())) for a in authors),
        default=0.0,
    )


def score_candidate(
    candidate: BookMetadata,
    *,
    title_hint: str,
    author_hint: str,
    position: int,
) -> float:
    """Score one search result against what the source claimed.

    Args:
        candidate: The catalogue result.
        title_hint: Title parsed from the source.
        author_hint: Author parsed from the source.
        position: Zero-based rank in the catalogue's own ordering.

    Returns:
        A score. Higher is better; the scale is arbitrary and only comparable
        within one search.
    """
    title = _title_score(title_hint, candidate.title) * _TITLE_WEIGHT
    author = _author_score(author_hint, (candidate.author,)) * _AUTHOR_WEIGHT
    # Catalogue relevance, decaying fast. It breaks ties without being able to
    # overturn a real title or author difference.
    rank = float(max(10 - position * 2, 0))
    return round(title + author + rank, 1)


def best_match(
    candidates: list[BookMetadata], *, title_hint: str, author_hint: str = ""
) -> BookMetadata | None:
    """Pick the catalogue result that best matches the source.

    Args:
        candidates: Search results, in the catalogue's own order.
        title_hint: Title parsed from the source.
        author_hint: Author parsed from the source, if any.

    Returns:
        The best candidate, or None when there were none.
    """
    if not candidates:
        return None

    ranked = sorted(
        (
            (
                score_candidate(
                    candidate,
                    title_hint=title_hint,
                    author_hint=author_hint,
                    position=index,
                ),
                index,
                candidate,
            )
            for index, candidate in enumerate(candidates)
        ),
        key=lambda item: (-item[0], item[1]),
    )

    score, _, winner = ranked[0]
    log.info("matched {!r} (asin={}, score={})", winner.title, winner.asin, score)
    return winner


def duration_matches(*, local_ms: int, remote_ms: int | None) -> bool:
    """Whether the local audio is the edition the catalogue is describing.

    Args:
        local_ms: Duration of the file on disk.
        remote_ms: Runtime the catalogue reports, or None when absent.

    Returns:
        True only when BOTH bounds hold. A missing runtime is False, not True:
        an unverifiable claim is not a verified one, and accepting it is how a
        wrong edition's chapters get written into a correct file.
    """
    if not remote_ms or local_ms <= 0:
        log.warning("cannot compare durations; one side is missing a runtime")
        return False

    delta_ms = abs(local_ms - remote_ms)
    delta_pct = delta_ms / remote_ms * 100.0

    if delta_pct > DURATION_TOLERANCE_PCT:
        log.warning(
            "rejecting fetched chapters: local {:.2f}h vs remote {:.2f}h ({:.2f}%)",
            local_ms / 3_600_000,
            remote_ms / 3_600_000,
            delta_pct,
        )
        return False

    if delta_ms / 1000.0 > DURATION_TOLERANCE_SEC:
        log.warning(
            "rejecting fetched chapters: {:.0f}s absolute difference",
            delta_ms / 1000.0,
        )
        return False

    return True


def _chapters_from_payload(payload: dict[str, Any]) -> ChapterSet:
    """Build a chapter table from an Audnexus response.

    Args:
        payload: The decoded response.

    Returns:
        The table. Empty when the response carried no usable chapters.
    """
    raw = payload.get("chapters") or []
    chapters: list[Chapter] = []

    for index, entry in enumerate(raw, start=1):
        start = entry.get("startOffsetMs")
        length = entry.get("lengthMs")
        if start is None or not length:
            continue
        chapters.append(
            Chapter(
                start_ms=int(start),
                end_ms=int(start) + int(length),
                title=str(entry.get("title") or f"Chapter {index}"),
            )
        )

    return ChapterSet(chapters=tuple(chapters), source="audnexus")


def fetch_chapters(
    client: httpx.Client, asin: str, *, local_ms: int, region: str = "us"
) -> ChapterSet:
    """Fetch a chapter table for an ASIN, verified against the local duration.

    Args:
        client: An httpx client, owned by the caller.
        asin: The book's ASIN.
        local_ms: Duration of the file on disk, for the edition check.
        region: Audnexus region code.

    Returns:
        The table, or an EMPTY table when the fetch failed or the durations
        disagree. Empty rather than an exception: no chapters is a normal
        outcome that the caller handles by keeping what it already had.
    """
    try:
        payload = get_json(
            client,
            f"{AUDNEXUS_BASE}/books/{asin}/chapters",
            params={"region": region},
        )
    # httpx.HTTPError ONLY -- exhausted retries, network trouble, and permanent
    # statuses such as a 404 for an ASIN Audnexus does not carry. TypeError is
    # deliberately NOT caught: get_json raises it on a contract change, and
    # catching it here would report a broken API as a book with no chapters.
    except httpx.HTTPError as exc:
        log.warning("audnexus chapter fetch failed for {}: {}", asin, exc)
        return ChapterSet()

    if not duration_matches(
        local_ms=local_ms, remote_ms=payload.get("runtimeLengthMs")
    ):
        return ChapterSet()

    chapters = _chapters_from_payload(payload)
    log.info("fetched {} chapter(s) for {}", len(chapters.chapters), asin)
    return chapters
