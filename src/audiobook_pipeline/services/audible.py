"""Searching the Audible catalogue and turning results into BookMetadata.

Purpose:
    One boundary, one job: ask Audible's public catalogue API a question and
    return validated models. Scoring and picking live in ``identify``; this
    module never decides which result is right, so a change to the matching
    rules cannot accidentally change what the API is asked for.

WHY THE RESPONSE IS MAPPED HERE AND NOWHERE ELSE
    The catalogue's JSON is wide, deeply nested, and full of fields that are
    present-but-empty rather than absent. Mapping it once, at the boundary,
    into ``BookMetadata`` is what keeps ``dict[str, Any]`` out of every stage
    downstream -- 68 such sites is what the pre-rewrite codebase carried, and
    this shape was the source of a good many of them.

Example:
    >>> _strip_html("<p>A <b>good</b> book.</p>")
    'A good book.'

See Also:
    - audiobook_pipeline.services.identify: scoring and match selection
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx
from loguru import logger

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.utils.http import get_json

log = logger.bind(stage="audible")

API_TEMPLATE = "https://api.audible.{region}/1.0/catalog/products"

#: Ten is enough for the right book to be present without making the scoring
#: pass meaningfully slower. The correct match is almost always in the top
#: three; the tail exists for the cases where the source's title is mangled.
MAX_RESULTS = 10

#: Everything needed to fill a BookMetadata in ONE call. Requesting these
#: piecemeal would mean several round trips per book, and on a 700-book run
#: that is the difference between minutes and an afternoon.
_RESPONSE_GROUPS = (
    "category_ladders,contributors,media,product_desc,"
    "product_attrs,product_extended_attrs,rating,series,product_details"
)

_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML markup from a catalogue summary.

    Args:
        text: Raw summary, which arrives with ``<p>`` and ``<b>`` markup.

    Returns:
        Plain text. Entities are unescaped too -- an ``&amp;`` written into a
        tag reads as a literal ``&amp;`` in every player.
    """
    return html.unescape(_TAG.sub("", text)).strip()


def _pick_series(series: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the most specific series when the catalogue returns several.

    Args:
        series: The catalogue's series entries.

    Returns:
        The chosen entry, or an empty dict. Prefers one carrying a POSITION:
        an umbrella series ("The Powder Mage Universe") usually has none, while
        the real sub-series does, and the position is what the library path
        needs.
    """
    with_position = [s for s in series if s.get("sequence")]
    if with_position:
        return with_position[0]
    return series[0] if series else {}


def _genres(ladders: list[dict[str, Any]]) -> tuple[str, ...]:
    """Extract genre names from the category ladders.

    Args:
        ladders: The catalogue's category ladders.

    Returns:
        Distinct genre names, order preserved. Plex reads the genre tag, and
        the pre-rewrite code hardcoded "Audiobook" -- which is the medium, not
        a genre, and made every book in the library identical to filter on.
    """
    names: list[str] = []
    for ladder in ladders:
        for rung in ladder.get("ladder") or []:
            name = str(rung.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return tuple(names)


def _cover_url(product_images: object) -> str:
    """Pick Audible's largest requested cover image, if present."""
    if not isinstance(product_images, dict):
        return ""
    for size in ("1024", "500"):
        value = product_images.get(size)
        if isinstance(value, str) and value:
            return value
    return ""


def _to_metadata(product: dict[str, Any]) -> BookMetadata | None:
    """Map one catalogue product into a validated model.

    Args:
        product: One entry from the catalogue response.

    Returns:
        The model, or None when the product has no title -- which is the one
        field with no sensible default, since it names the book.
    """
    title = str(product.get("title") or "").strip()
    if not title:
        return None

    authors = [str(a.get("name") or "") for a in (product.get("authors") or [])]
    narrators = [str(n.get("name") or "") for n in (product.get("narrators") or [])]
    series = _pick_series(product.get("series") or [])
    release_date = str(product.get("release_date") or "")

    return BookMetadata(
        title=title,
        author=", ".join(a for a in authors if a),
        narrator=", ".join(n for n in narrators if n),
        asin=str(product.get("asin") or ""),
        series=str(series.get("title") or ""),
        series_position=str(series.get("sequence") or ""),
        release_year=int(release_date[:4]) if release_date[:4].isdigit() else None,
        publisher=str(product.get("publisher_name") or ""),
        summary=_strip_html(str(product.get("publisher_summary") or "")),
        copyright=str(product.get("copyright") or ""),
        genres=_genres(product.get("category_ladders") or []),
        cover_url=_cover_url(product.get("product_images")),
    )


def search(
    client: httpx.Client, query: str, *, region: str = "com"
) -> list[BookMetadata]:
    """Search the Audible catalogue.

    Args:
        client: An httpx client, owned by the caller, so a batch run shares one
            connection pool and tests can pass a transport that never touches
            the network.
        query: Free-text search, normally "title author".
        region: Audible regional domain suffix (``com``, ``co.uk``, ``de``).

    Returns:
        Validated candidates in the catalogue's own relevance order, which
        ``identify.best_match`` then re-scores. An empty list when the search
        failed -- a book that cannot be identified is still convertible, so a
        failed lookup must not raise past this point.
    """
    try:
        payload = get_json(
            client,
            API_TEMPLATE.format(region=region),
            params={
                "keywords": query,
                "num_results": str(MAX_RESULTS),
                "products_sort_by": "Relevance",
                "response_groups": _RESPONSE_GROUPS,
                "image_sizes": "500,1024",
            },
        )
    # httpx.HTTPError ONLY. TypeError is deliberately not caught: get_json
    # raises it when the service returns a list where an object was documented,
    # which is a contract change, not missing data -- and catching it here also
    # swallowed a wrong-signature bug into a warning while this was being
    # written (observed 2026-08-02: "get_json() missing 1 required positional
    # argument" reported as a failed search rather than as the error it was).
    except httpx.HTTPError as exc:
        log.warning("audible search failed for {!r}: {}", query, exc)
        return []

    products = payload.get("products") or []
    results = [
        metadata
        for product in products
        if (metadata := _to_metadata(product)) is not None
    ]
    log.debug("audible returned {} candidate(s) for {!r}", len(results), query)
    return results
