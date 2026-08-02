"""Audible catalog search client.

Queries the Audible product catalog API and returns structured results
for fuzzy matching and metadata resolution. Extracts expanded metadata
(subtitle, publisher, copyright, genre, language) for Plex-compatible tagging.
"""

import re

import httpx
from loguru import logger


def search(query: str, region: str = "com") -> list[dict]:
    """Search Audible catalog API, return up to 10 results.

    Each result dict contains: asin, title, subtitle, authors (list),
    author_str, series, position, narrators, narrator_str, release_date,
    year, cover_url, publisher_summary, publisher_name, copyright,
    language, genre.
    """
    api_base = f"https://api.audible.{region}/1.0"
    params = {
        "keywords": query,
        "num_results": "10",
        "products_sort_by": "Relevance",
        "response_groups": (
            "category_ladders,contributors,media,product_desc,"
            "product_attrs,product_extended_attrs,rating,series,"
            "product_details"
        ),
        "image_sizes": "500,1024",
    }

    logger.debug(f"Audible search: query={query!r} region={region}")

    try:
        resp = httpx.get(
            f"{api_base}/catalog/products",
            params=params,
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(f"Audible API error: {e}")
        return []

    data = resp.json()
    products = data.get("products", [])

    results = []
    for p in products:
        authors = [a.get("name", "") for a in (p.get("authors") or [])]
        series_info = _pick_best_series(p.get("series") or [])
        # Extract cover art URL -- prefer larger sizes
        images = p.get("product_images") or {}
        cover_url = images.get("1024", images.get("500", ""))

        narrators = [n.get("name", "") for n in (p.get("narrators") or [])]
        release_date = p.get("release_date", "")

        # Extract genre from category_ladders (walk ladder, join with /)
        genre = _extract_genre(p.get("category_ladders") or [])

        # Publisher summary -- strip HTML tags
        raw_summary = p.get("publisher_summary", "") or ""
        publisher_summary = _strip_html(raw_summary)

        # Store all series for AI evidence (helps prefer sub-series over umbrella)
        all_series = [
            {"name": s.get("title", ""), "position": s.get("sequence", "")}
            for s in (p.get("series") or [])
            if s.get("title")
        ]

        results.append(
            {
                "asin": p.get("asin", ""),
                "title": p.get("title", ""),
                "subtitle": p.get("subtitle", "") or "",
                "authors": authors,
                "author_str": ", ".join(authors),
                "narrators": narrators,
                "narrator_str": ", ".join(narrators),
                "series": series_info.get("title", "") if series_info else "",
                "position": series_info.get("sequence", "") if series_info else "",
                "all_series": all_series,
                "release_date": release_date,
                "year": release_date[:4] if release_date else "",
                "cover_url": cover_url,
                "publisher_summary": publisher_summary,
                "publisher_name": p.get("publisher_name", "") or "",
                "copyright": p.get("copyright", "") or "",
                "language": p.get("language", "") or "",
                "genre": genre,
            }
        )

    logger.debug(f"Audible results: {len(results)} products")
    return results


def _pick_best_series(series_list: list[dict]) -> dict | None:
    """Pick the most specific series when Audible returns multiple.

    Audible often lists both a specific sub-series (e.g., "Liveship Traders")
    and an umbrella super-series (e.g., "Realms of the Elderlings"). The
    sub-series has a lower position number (Book 2) while the super-series
    has a high one (Book 5). Prefer the lowest position to get the specific
    series.
    """
    if not series_list:
        return None
    if len(series_list) == 1:
        return series_list[0]

    def _sort_key(s: dict) -> float:
        seq = s.get("sequence", "") or ""
        try:
            return float(seq)
        except (ValueError, TypeError):
            return 999.0

    best = min(series_list, key=_sort_key)
    if len(series_list) > 1:
        logger.debug(
            f"Multi-series: picked '{best.get('title')}' #{best.get('sequence')} "
            f"from {[s.get('title') for s in series_list]}"
        )
    return best


def _extract_genre(category_ladders: list[dict]) -> str:
    """Extract genre string from Audible category_ladders.

    Walks the first ladder and joins category names with '/'.
    Example: [{"ladder": [{"name": "Science Fiction"}, {"name": "Space Opera"}]}]
    -> "Science Fiction/Space Opera"
    """
    if not category_ladders:
        return ""
    ladder = category_ladders[0].get("ladder", [])
    names = [step.get("name", "") for step in ladder if step.get("name")]
    return "/".join(names) if names else ""


def _strip_html(text: str) -> str:
    """Strip HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()
