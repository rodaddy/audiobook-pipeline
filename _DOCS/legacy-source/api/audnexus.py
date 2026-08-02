"""Audnexus API client -- chapter timings for books that carry none.

Audnexus (https://audnex.us) aggregates audiobook metadata, including the
chapter tables Audible ships with a title. It is the source for the one case
this pipeline otherwise cannot handle: a book that arrives as loose audio with
no chapter marks anywhere, where the file boundaries are encoding splits rather
than chapters.

This is a port of the working logic in the repo's original bash implementation
(lib/audnexus.sh), which the Python rewrite dropped -- keeping only the config
keys. Verified live 2026-08-01: the API answers, the payload shape is
unchanged, and the repo (laxamentumtech/audnexus) was pushed to the previous
day, so this is current rather than revived-stale.

WHAT IS DELIBERATELY NOT DONE HERE
    No proportional scaling of timings onto a differently-sized local file, and
    no automatic subtraction of brandIntro/brandOutro. Measured across the six
    books needing chapters, five matched Audnexus runtime within 0.28% and the
    sixth was off by 13.42% -- a different edition, not a stretchable one.
    There is no middle ground in that data to scale across, so a mismatch is
    REJECTED rather than fitted. Scaling a wrong-edition table produces a
    plausible, monotonic, entirely wrong chapter map, which is the worst
    outcome available: it looks correct and is not.
"""

from __future__ import annotations

from itertools import pairwise

import httpx
from loguru import logger

log = logger.bind(stage="audnexus")

API_BASE = "https://api.audnex.us"

# How far the local audio may differ from the edition Audnexus describes.
#
# The bash used 5%. That is far too loose for what the data actually looks
# like: measured 2026-08-01 over the books in this library, the good matches
# landed at 0.00, 0.00, 0.13, 0.18 and 0.28 percent, and the one bad match at
# 13.42 percent. Nothing sits in between, so a tight bound costs nothing real
# and refuses a wrong edition that 5% would have accepted and written.
CHAPTER_DURATION_TOLERANCE_PCT = 1.0

# Even inside the percentage bound, a large absolute gap means the runtimes
# only look similar because the book is long. Ten minutes adrift is not the
# same recording.
CHAPTER_DURATION_TOLERANCE_SEC = 600.0

REQUEST_TIMEOUT = 30.0


def fetch_chapters(asin: str, region: str = "us") -> dict | None:
    """Fetch the raw chapter payload for an ASIN, or None.

    None covers every failure equally -- unknown ASIN (a 404 is normal and
    expected for anything Audible does not carry), network trouble, malformed
    JSON. The caller falls back to file-boundary chapters, so a miss here is
    a smaller outcome, not an error.
    """
    url = f"{API_BASE}/books/{asin}/chapters"
    try:
        resp = httpx.get(url, params={"region": region}, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as e:
        log.warning(f"Audnexus request failed for {asin}: {e}")
        return None

    if resp.status_code == httpx.codes.NOT_FOUND:
        log.info(f"Audnexus has no chapters for {asin}")
        return None
    if resp.status_code != httpx.codes.OK:
        log.warning(f"Audnexus returned {resp.status_code} for {asin}")
        return None

    try:
        data = resp.json()
    except ValueError:
        log.warning(f"Audnexus returned unparseable JSON for {asin}")
        return None

    if not isinstance(data, dict) or not data.get("chapters"):
        log.info(f"Audnexus payload for {asin} carries no chapters")
        return None

    return data


def _duration_matches(payload: dict, local_duration_sec: float) -> bool:
    """True when the local audio is the edition Audnexus is describing.

    Both a relative AND an absolute bound must hold. Percentage alone lets a
    20-hour book drift ten minutes and still pass; seconds alone would reject
    a short book over a trivial difference.
    """
    runtime_ms = payload.get("runtimeLengthMs")
    if not runtime_ms or local_duration_sec <= 0:
        log.warning("Cannot compare durations -- missing runtime on one side")
        return False

    remote_sec = runtime_ms / 1000.0
    delta_sec = abs(local_duration_sec - remote_sec)
    delta_pct = delta_sec / remote_sec * 100.0

    if delta_pct > CHAPTER_DURATION_TOLERANCE_PCT:
        log.warning(
            f"Rejecting Audnexus chapters: local {local_duration_sec / 3600:.2f}h "
            f"vs remote {remote_sec / 3600:.2f}h ({delta_pct:.2f}% > "
            f"{CHAPTER_DURATION_TOLERANCE_PCT}%) -- probably a different edition"
        )
        return False

    if delta_sec > CHAPTER_DURATION_TOLERANCE_SEC:
        log.warning(
            f"Rejecting Audnexus chapters: {delta_sec:.0f}s absolute difference "
            f"exceeds {CHAPTER_DURATION_TOLERANCE_SEC:.0f}s"
        )
        return False

    log.debug(f"Audnexus duration matches within {delta_pct:.2f}% / {delta_sec:.0f}s")
    return True


def get_chapters(
    asin: str,
    local_duration_sec: float,
    region: str = "us",
) -> list[dict]:
    """Chapter marks for an ASIN as [{start_ms, end_ms, title}], or [].

    Returns [] rather than raising on every rejection path, so a caller can
    treat "no usable remote chapters" as one condition.

    Gates, all of which must pass before a single mark is returned:
      * the payload exists and carries chapters
      * isAccurate is not false -- Audnexus flags tables it does not trust
      * the local audio duration matches the edition described
      * each mark is monotonic and inside the local audio

    Offsets are used RAW. brandIntroDurationMs/brandOutroDurationMs are read
    only for the log line: Audible's startOffsetMs values already account for
    branding, and subtracting it again shifts every chapter earlier by a few
    seconds -- the kind of error that is invisible in a spot check and wrong
    through the whole book.
    """
    payload = fetch_chapters(asin, region)
    if not payload:
        return []

    if payload.get("isAccurate") is False:
        log.warning(f"Audnexus flags its own chapter data for {asin} as inaccurate")
        return []

    if not _duration_matches(payload, local_duration_sec):
        return []

    log.debug(
        f"brandIntro={payload.get('brandIntroDurationMs')}ms "
        f"brandOutro={payload.get('brandOutroDurationMs')}ms (not subtracted)"
    )

    local_ms = int(local_duration_sec * 1000)
    chapters: list[dict] = []
    for idx, raw in enumerate(payload["chapters"], start=1):
        start = raw.get("startOffsetMs")
        length = raw.get("lengthMs")
        if not isinstance(start, int) or not isinstance(length, int):
            log.warning(f"Skipping malformed Audnexus chapter {idx} for {asin}")
            continue

        end = min(start + length, local_ms)
        if start >= local_ms or end <= start:
            log.warning(f"Skipping out-of-range Audnexus chapter {idx} for {asin}")
            continue

        title = (raw.get("title") or "").strip() or f"Chapter {idx}"
        chapters.append({"start_ms": start, "end_ms": end, "title": title})

    if not chapters:
        log.warning(f"No usable chapters survived validation for {asin}")
        return []

    # A table whose marks do not advance is corrupt however plausible each
    # entry looked on its own.
    for prev, nxt in pairwise(chapters):
        if nxt["start_ms"] < prev["start_ms"]:
            log.warning(f"Audnexus chapters for {asin} are not monotonic -- rejecting")
            return []

    log.info(f"Audnexus supplied {len(chapters)} chapters for {asin}")
    return chapters
