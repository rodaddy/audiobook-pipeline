"""JSON manifest state machine -- backward compatible with the bash version."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from .models import (
    PRE_COMPLETED_STAGES,
    STAGE_ORDER,
    PipelineMode,
    Stage,
    StageStatus,
    ErrorCategory,
)
from .errors import ManifestError

log = logger.bind(stage="manifest")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Manifest:
    """Manage per-book JSON manifest files.

    File format is identical to the bash implementation so manifests
    created by either version are interchangeable.
    """

    def __init__(self, manifest_dir: Path) -> None:
        self.manifest_dir = manifest_dir

    def ensure_dir(self) -> None:
        """Create manifest directory if it doesn't exist."""
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def path(self, book_hash: str) -> Path:
        return self.manifest_dir / f"{book_hash}.json"

    # -- Read operations --

    def read(self, book_hash: str) -> dict | None:
        p = self.path(book_hash)
        if not p.exists():
            return None
        try:
            log.debug(f"Reading manifest {book_hash}")
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.error(f"Failed to read manifest {book_hash}: {exc}")
            raise ManifestError(f"Failed to read manifest {p}: {exc}") from exc

    def read_field(self, book_hash: str, field: str) -> Any:
        data = self.read(book_hash)
        if data is None:
            return None
        # Support dotted paths like "stages.validate.status"
        parts = field.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    # -- Write operations (all use atomic writes) --

    def _atomic_write(self, book_hash: str, data: dict) -> None:
        target = self.path(book_hash)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.manifest_dir), suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            log.debug(f"Writing manifest {book_hash}")
            os.replace(tmp_path, target)
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError as cleanup_err:
                log.warning(f"Failed to cleanup temp file {tmp_path}: {cleanup_err}")
            raise

    def create(
        self, book_hash: str, source_path: str, mode: PipelineMode,
    ) -> dict:
        created_at = _utcnow()
        data: dict[str, Any] = {
            "book_hash": book_hash,
            "source_path": source_path,
            "created_at": created_at,
            "mode": str(mode),
            "status": "pending",
            "retry_count": 0,
            "max_retries": 3,
            "last_error": {},
            "stages": {
                stage.value: {"status": "pending"}
                for stage in Stage
            },
            "metadata": {},
        }

        # For non-convert modes, pre-complete early stages
        pre = PRE_COMPLETED_STAGES.get(mode, [])
        for stage in pre:
            data["stages"][stage.value]["status"] = "completed"
            data["stages"][stage.value]["completed_at"] = created_at
            if stage == Stage.CONVERT:
                data["stages"][stage.value]["output_file"] = source_path

        self.ensure_dir()
        self._atomic_write(book_hash, data)
        log.info(f"Created manifest {book_hash} mode={mode}")
        return data

    def update(self, book_hash: str, updates: dict) -> None:
        data = self.read(book_hash)
        if data is None:
            raise ManifestError(f"Manifest not found for {book_hash}")
        data.update(updates)
        log.debug(f"Updated manifest {book_hash}")
        self._atomic_write(book_hash, data)

    def set_stage(
        self, book_hash: str, stage: Stage, status: StageStatus,
    ) -> None:
        data = self.read(book_hash)
        if data is None:
            raise ManifestError(f"Manifest not found for {book_hash}")

        log.debug(f"Stage {stage.value} -> {status} for {book_hash}")
        data["stages"][stage.value]["status"] = str(status)
        if status == StageStatus.COMPLETED:
            data["stages"][stage.value]["completed_at"] = _utcnow()

        self._atomic_write(book_hash, data)

    def check_status(self, book_hash: str) -> str:
        """Return the book's processing status.

        Returns "new" if no manifest exists, otherwise the status field value
        ("pending", "completed", "failed", "processing").
        """
        log.debug(f"check_status book_hash={book_hash}")
        data = self.read(book_hash)
        if data is None:
            return "new"
        return data.get("status", "pending")

    def get_next_stage(
        self, book_hash: str, mode: PipelineMode,
    ) -> Stage | None:
        """Find the next stage that hasn't been completed.

        Returns None if all stages for this mode are completed.
        """
        log.debug(f"get_next_stage book_hash={book_hash} mode={mode}")
        data = self.read(book_hash)
        if data is None:
            raise ManifestError(f"Manifest not found for {book_hash}")

        stages = STAGE_ORDER.get(mode, [])
        for stage in stages:
            stage_status = (
                data.get("stages", {})
                .get(stage.value, {})
                .get("status", "pending")
            )
            if stage_status != "completed":
                return stage
        return None

    def increment_retry(self, book_hash: str) -> None:
        data = self.read(book_hash)
        if data is None:
            raise ManifestError(f"Manifest not found for {book_hash}")
        new_count = data.get("retry_count", 0) + 1
        log.warning(f"increment_retry book_hash={book_hash} new_count={new_count}")
        data["retry_count"] = new_count
        self._atomic_write(book_hash, data)

    def set_error(
        self,
        book_hash: str,
        stage: str,
        exit_code: int,
        category: ErrorCategory,
        message: str,
    ) -> None:
        log.error(
            f"set_error book_hash={book_hash} stage={stage} "
            f"category={category} message={message}"
        )
        data = self.read(book_hash)
        if data is None:
            raise ManifestError(f"Manifest not found for {book_hash}")

        data["last_error"] = {
            "timestamp": _utcnow(),
            "stage": stage,
            "exit_code": exit_code,
            "category": str(category),
            "message": message,
        }
        self._atomic_write(book_hash, data)
