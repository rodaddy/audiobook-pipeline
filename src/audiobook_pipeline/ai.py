"""AI-assisted metadata resolution using OpenAI-compatible APIs.

Provides intelligent disambiguation when path parsing, embedded tags,
and Audible search produce conflicting or incomplete metadata.
Works with any OpenAI-compatible endpoint (OpenAI, LiteLLM, Ollama).
"""

from __future__ import annotations

import re
import uuid

from loguru import logger


def get_client(base_url: str, api_key: str):
    """Return an OpenAI client configured for the given endpoint, or None.

    Returns None if base_url is empty (AI disabled).
    """
    if not base_url:
        return None

    from openai import OpenAI

    # OpenAI SDK expects base_url WITHOUT /v1 -- it appends that itself
    clean_url = base_url.rstrip("/")
    if clean_url.endswith("/v1"):
        clean_url = clean_url[:-3].rstrip("/")

    return OpenAI(
        base_url=clean_url,
        api_key=api_key or "not-needed",
    )


def needs_resolution(
    path_metadata: dict,
    tag_metadata: dict,
    audible_result: dict | None,
) -> bool:
    """Decide if AI resolution should fire.

    Returns True when metadata sources conflict or are all empty.
    """
    authors = set()
    for source in [
        path_metadata.get("author", ""),
        tag_metadata.get("author", ""),
        (audible_result or {}).get("author", ""),
    ]:
        cleaned = source.strip().lower()
        if cleaned and cleaned not in ("unknown", "_unsorted", "various"):
            authors.add(cleaned)

    # Conflict: multiple different non-empty authors
    if len(authors) > 1:
        return True
    # All empty: no author found anywhere
    if len(authors) == 0:
        return True
    return False


def resolve(
    path_metadata: dict,
    tag_metadata: dict,
    audible_candidates: list[dict] | None,
    model: str,
    client,
    source_filename: str = "",
) -> dict | None:
    """Resolve ALL metadata (author, title, series, position) using AI.

    Takes all available evidence and asks the AI to reason about the correct
    metadata. Returns a dict with resolved fields, or None if AI is unavailable
    or can't determine.
    """
    if client is None:
        return None

    evidence_parts = []

    # Source file identification -- leads the prompt to defeat semantic caching
    if source_filename:
        evidence_parts.append(f"Source filename: {source_filename!r}")

    # Path evidence
    if path_metadata.get("author"):
        evidence_parts.append(f"File path suggests author: {path_metadata['author']!r}")
    if path_metadata.get("title"):
        evidence_parts.append(f"File path title: {path_metadata['title']!r}")
    if path_metadata.get("series"):
        evidence_parts.append(f"File path series: {path_metadata['series']!r}")
    if path_metadata.get("position"):
        evidence_parts.append(f"File path position: {path_metadata['position']!r}")

    # Tag evidence
    if tag_metadata.get("author"):
        evidence_parts.append(f"Embedded tags artist: {tag_metadata['author']!r}")
    if tag_metadata.get("album"):
        evidence_parts.append(f"Tag album: {tag_metadata['album']!r}")
    if tag_metadata.get("title"):
        evidence_parts.append(f"Tag title: {tag_metadata['title']!r}")

    # Audible evidence
    if audible_candidates:
        evidence_parts.append("\nAudible search results:")
        for i, cand in enumerate(audible_candidates[:5], 1):
            parts = [f'{i}. "{cand["title"]}" by {cand["author_str"]}']
            if cand.get("series"):
                parts.append(f"(Series: {cand['series']}")
                if cand.get("position"):
                    parts[-1] += f" #{cand['position']}"
                parts[-1] += ")"
            if cand.get("score"):
                parts.append(f"[score: {cand['score']:.0f}]")
            evidence_parts.append(" ".join(parts))

    if not evidence_parts:
        return None

    evidence_text = "\n".join(evidence_parts)
    nonce = uuid.uuid4().hex[:8]

    # Unique evidence leads the prompt to defeat prefix-based semantic caching
    prompt = (
        f"[{nonce}] Resolve metadata for: {source_filename!r}\n\n"
        f"Evidence:\n{evidence_text}\n\n"
        "Determine the correct audiobook metadata from the evidence above.\n"
        "- Author: real person's first and last name (not series/brand names)\n"
        "- Title: the specific book title (not the series name)\n"
        "- Series: series name if applicable, otherwise NONE\n"
        "- Position: book number in series if applicable, otherwise NONE\n\n"
        "Reply in this exact format (one per line, no extra text):\n"
        "AUTHOR: <name>\n"
        "TITLE: <title>\n"
        "SERIES: <series or NONE>\n"
        "POSITION: <number or NONE>"
    )

    logger.bind(stage="ai").debug("Resolving metadata conflict...")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.1,
            extra_headers={"Cache-Control": "no-cache"},
        )
        content = response.choices[0].message.content.strip()
        return _parse_resolve_response(content)

    except Exception as e:
        logger.bind(stage="ai").warning(f"Metadata resolution failed: {e}")
        return None


def disambiguate(
    candidates: list[dict],
    title_hint: str,
    author_hint: str,
    model: str,
    client,
) -> dict | None:
    """Pick the best Audible match from candidates using AI.

    Returns the selected candidate dict, or None if AI can't determine.
    """
    if client is None or not candidates:
        return None

    candidate_text = "\n".join(
        f'{i+1}. "{c["title"]}" by {c["author_str"]} (ASIN: {c["asin"]})'
        for i, c in enumerate(candidates[:5])
    )

    nonce = uuid.uuid4().hex[:8]

    prompt = (
        f'[{nonce}] Find the best match for: "{title_hint}"'
        + (f" by {author_hint}" if author_hint else "")
        + f"\n\nSearch results:\n{candidate_text}"
        + "\n\nWhich result (1-5) is the best match? Reply with ONLY the number,"
        + " or 0 if none match. No explanation."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0,
            extra_headers={"Cache-Control": "no-cache"},
        )
        content = response.choices[0].message.content.strip()

        match = re.search(r"[0-5]", content)
        if match:
            pick = int(match.group())
            if pick == 0:
                return None
            idx = pick - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
    except Exception as e:
        logger.bind(stage="ai").warning(f"AI disambiguation failed: {e}")

    return None


def _parse_resolve_response(content: str) -> dict | None:
    """Parse the structured AI response into a metadata dict."""
    result = {}

    for line in content.splitlines():
        line = line.strip()
        if line.upper().startswith("AUTHOR:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val and val.upper() != "UNKNOWN":
                result["author"] = val
        elif line.upper().startswith("TITLE:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val and val.upper() != "UNKNOWN":
                result["title"] = val
        elif line.upper().startswith("SERIES:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val and val.upper() not in ("NONE", "UNKNOWN", "N/A", ""):
                result["series"] = val
        elif line.upper().startswith("POSITION:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val and val.upper() not in ("NONE", "UNKNOWN", "N/A", ""):
                result["position"] = val

    # Must have at least author to be useful
    if "author" not in result:
        return None

    # Clean AI-produced title junk
    if "title" in result:
        result["title"] = re.sub(
            r"\s*\((?:The\s+)?Audio\s*Book\)", "", result["title"],
            flags=re.IGNORECASE,
        )
        result["title"] = re.sub(
            r"\s*\(Unabridged\)", "", result["title"],
            flags=re.IGNORECASE,
        )
        result["title"] = result["title"].strip()

    # Normalize position: "01" -> "1"
    if "position" in result and result["position"].isdigit():
        result["position"] = str(int(result["position"]))

    return result
