"""Prepare bounded AI evidence and map validated choices to catalogue records."""

from __future__ import annotations

from pydantic import SecretStr

from audiobook_pipeline.config import Settings
from audiobook_pipeline.models.ai import AiCandidate, AiEvidence, AiResolverOptions
from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.models.stage import PipelineLevel
from audiobook_pipeline.services.ai import AiResolver

_MAX_EVIDENCE_TEXT = 240
_MAX_POSITION_TEXT = 40
_MAX_ASIN_TEXT = 40


def resolver_for(
    config: Settings, injected: AiResolver | None
) -> tuple[AiResolver | None, bool]:
    """Return the AI/FULL resolver and whether this call must close it."""
    if config.level not in {PipelineLevel.AI, PipelineLevel.FULL}:
        return None, False
    if injected is not None:
        return injected, False
    options = AiResolverOptions(
        base_url=config.ai.base_url,
        api_key=SecretStr(config.ai.api_key),
        model=config.ai.model,
        all_books=config.ai.all_books,
        timeout_seconds=config.ai.timeout_seconds,
    )
    return AiResolver(options), True


def evidence_for(
    book: BookDirectory,
    claim: ParsedPath,
    match: BookMetadata,
    candidates: list[BookMetadata],
) -> AiEvidence:
    """Build bounded, sanitized evidence without making AI metadata canonical."""
    conflict = bool(
        not claim.author
        or (
            claim.author
            and match.author
            and claim.author.casefold() != match.author.casefold()
        )
        or (claim.title and claim.title.casefold() != match.title.casefold())
    )
    return AiEvidence(
        source_filename=book.identity_path.name,
        source_directory=str(book.identity_path.parent),
        path_author=claim.author,
        path_title=claim.title,
        path_series=claim.series,
        path_position=claim.position,
        catalog_author=match.author,
        resolution_requested=conflict,
        candidates=_offered_candidates(candidates),
    )


def selected_match(
    resolver: AiResolver | None,
    level: PipelineLevel,
    evidence: AiEvidence,
    candidates: list[BookMetadata],
) -> BookMetadata | None:
    """Map only a validated offered selection back to its full catalogue record."""
    if resolver is None:
        return None
    decision = resolver.resolve(evidence, level)
    if decision.candidate is None:
        return None
    return next(
        (
            candidate
            for candidate in candidates[:5]
            if candidate.asin == decision.candidate.asin
        ),
        None,
    )


def _offered_candidates(candidates: list[BookMetadata]) -> tuple[AiCandidate, ...]:
    """Convert only the top five unique, usable catalogue results for AI."""
    offered: list[AiCandidate] = []
    seen_asins: set[str] = set()
    for candidate in candidates[:5]:
        title = _safe_candidate_text(candidate.title)
        author = _safe_candidate_text(candidate.author)
        if (
            not _is_safe_candidate_asin(candidate.asin)
            or not title
            or not author
            or candidate.asin in seen_asins
        ):
            continue
        seen_asins.add(candidate.asin)
        offered.append(
            AiCandidate(
                candidate_id=candidate.asin,
                asin=candidate.asin,
                title=title,
                author=author,
                series=_safe_candidate_text(candidate.series),
                series_position=_safe_candidate_text(
                    candidate.series_position, _MAX_POSITION_TEXT
                ),
            )
        )
    return tuple(offered)


def _safe_candidate_text(value: str, limit: int = _MAX_EVIDENCE_TEXT) -> str:
    """Flatten untrusted catalogue text and cap it before Pydantic validation."""
    return value.replace("\n", " ").replace("\r", " ").strip()[:limit]


def _is_safe_candidate_asin(value: str) -> bool:
    """Accept only identities that AiCandidate can retain exactly."""
    return (
        bool(value)
        and len(value) <= _MAX_ASIN_TEXT
        and "\n" not in value
        and "\r" not in value
    )
