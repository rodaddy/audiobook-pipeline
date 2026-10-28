"""Validate and reconcile durable watch quarantine recovery reservations."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.watch import WatchClaim

log = logger.bind(component="watch")


def quarantine_recovery_state(claim: WatchClaim) -> str:
    """Classify a validated durable transition without modifying its data."""
    assert claim.quarantine_dir is not None
    try:
        has_quarantined_data = any(claim.quarantine_dir.iterdir())
    except OSError:
        log.warning("Watch quarantine recovery failed: destination_unavailable")
        return "deferred"
    if not claim.source.exists():
        return "moved" if has_quarantined_data else "deferred"
    return "partial" if has_quarantined_data else "unmoved"


def safe_recovery_reservation(quarantine_root: Path, claim: WatchClaim) -> bool:
    """Require a literal, non-symlinked direct child reservation of its root."""
    if claim.quarantine_dir is None:
        return False
    root = quarantine_root.absolute()
    try:
        parts = claim.quarantine_dir.absolute().relative_to(root).parts
    except ValueError:
        log.warning("Watch quarantine recovery failed: destination_outside_root")
        return False
    if len(parts) != 1 or parts[0] in {".", ".."}:
        return False
    if any(
        path.is_symlink() or not path.is_dir() for path in (root, claim.quarantine_dir)
    ):
        return False
    try:
        return not any(member.is_symlink() for member in claim.quarantine_dir.iterdir())
    except OSError:
        log.warning("Watch quarantine recovery failed: destination_unavailable")
        return False
