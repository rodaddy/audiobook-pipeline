"""Audible catalog search client.

Queries the Audible product catalog API and returns structured results
for fuzzy matching and metadata resolution.
"""

import httpx
from loguru import logger


def search(query: str, region: str = "com") -> list[dict]:
    """Search Audible catalog API, return up to 10 results.

    Each result dict contains: asin, title, authors (list), author_str,
    series, position.
    """
    api_base = f"https://api.audible.{region}/1.0"
    params = {
        "keywords": query,
        "num_results": "10",
        "products_sort_by": "Relevance",
        "response_groups": "contributors,media,product_desc,product_attrs,series",
        "image_sizes": "100",
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
        series_info = (p.get("series") or [None])[0]
        results.append({
            "asin": p.get("asin", ""),
            "title": p.get("title", ""),
            "authors": authors,
            "author_str": ", ".join(authors),
            "series": series_info.get("title", "") if series_info else "",
            "position": series_info.get("sequence", "") if series_info else "",
        })

    logger.debug(f"Audible results: {len(results)} products")
    return results
