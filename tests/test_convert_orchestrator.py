"""Tests for convert_orchestrator -- CPU-aware parallel batch processor."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.convert_orchestrator import ConvertOrchestrator
from audiobook_pipeline.models import BatchResult


class TestConvertOrchestrator:
    def _make_config(self, tmp_path):
        return PipelineConfig(
            _env_file=None,
            work_dir=tmp_path / "work",
            nfs_output_dir=tmp_path / "output",
            dry_run=True,
        )

    def test_empty_batch(self, tmp_path):
        config = self._make_config(tmp_path)
        orch = ConvertOrchestrator(config)
        result = orch.run_batch([])
        assert result.total == 0
        assert result.completed == 0
        assert result.failed == 0

    def test_max_workers_auto(self, tmp_path):
        config = self._make_config(tmp_path)
        orch = ConvertOrchestrator(config)
        workers = orch._calculate_max_workers()
        cpu_count = os.cpu_count() or 1
        assert workers == max(1, min(4, cpu_count // 3))

    def test_max_workers_configured(self, tmp_path):
        config = self._make_config(tmp_path)
        config.max_parallel_converts = 3
        orch = ConvertOrchestrator(config)
        assert orch._calculate_max_workers() == 3

    def test_threads_per_worker_single(self, tmp_path):
        config = self._make_config(tmp_path)
        orch = ConvertOrchestrator(config)
        assert orch._threads_per_worker(1) == 0  # all cores

    def test_threads_per_worker_multiple(self, tmp_path):
        config = self._make_config(tmp_path)
        orch = ConvertOrchestrator(config)
        cpu_count = os.cpu_count() or 1
        threads = orch._threads_per_worker(4)
        assert threads == max(1, (cpu_count - 1) // 4)

    def test_cpu_load_pct_returns_float(self, tmp_path):
        config = self._make_config(tmp_path)
        orch = ConvertOrchestrator(config)
        pct = orch._cpu_load_pct()
        assert isinstance(pct, float)
        assert pct >= 0
