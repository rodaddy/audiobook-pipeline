"""Tests for config.py -- defaults, env var overrides."""

import os
from pathlib import Path

import pytest

from audiobook_pipeline.config import PipelineConfig

# Env vars that pydantic-settings reads -- must be cleaned for default tests
_CONFIG_ENV_VARS = [
    "WORK_DIR", "MANIFEST_DIR", "OUTPUT_DIR", "LOG_DIR", "ARCHIVE_DIR",
    "LOCK_DIR", "NFS_OUTPUT_DIR", "MAX_BITRATE", "CHANNELS", "CODEC",
    "FILE_OWNER", "FILE_MODE", "DIR_MODE", "DRY_RUN", "FORCE", "VERBOSE",
    "CLEANUP_WORK_DIR", "LOG_LEVEL", "METADATA_SOURCE", "AUDIBLE_REGION",
    "AUDNEXUS_REGION", "MAX_RETRIES",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove pipeline env vars so defaults tests see actual defaults."""
    for var in _CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Also clear ASIN_SEARCH_THRESHOLD and AI vars that .env may set
    for var in ["ASIN_SEARCH_THRESHOLD", "AI_ALL", "OPENAI_BASE_URL",
                "OPENAI_API_KEY", "OPENAI_MODEL"]:
        monkeypatch.delenv(var, raising=False)


class TestDefaults:
    def test_default_values(self):
        config = PipelineConfig(_env_file=None)
        assert config.max_bitrate == 128
        assert config.channels == 1
        assert config.codec == "aac"
        assert config.dry_run is False
        assert config.force is False
        assert config.verbose is False
        assert config.cleanup_work_dir is True
        assert config.log_level == "INFO"
        assert config.max_retries == 3
        assert config.file_mode == "644"
        assert config.dir_mode == "755"

    def test_default_paths(self):
        config = PipelineConfig(_env_file=None)
        assert config.work_dir == Path("/var/lib/audiobook-pipeline/work")
        assert config.manifest_dir == Path("/var/lib/audiobook-pipeline/manifests")
        assert config.nfs_output_dir == Path("/mnt/media/AudioBooks")

    def test_metadata_defaults(self):
        config = PipelineConfig(_env_file=None)
        assert config.metadata_source == "audible"
        assert config.audible_region == "com"
        assert config.audnexus_region == "us"
        assert config.metadata_skip is False


class TestOverrides:
    def test_constructor_override(self):
        config = PipelineConfig(_env_file=None, dry_run=True, max_bitrate=64)
        assert config.dry_run is True
        assert config.max_bitrate == 64

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("MAX_BITRATE", "96")
        monkeypatch.setenv("DRY_RUN", "true")
        config = PipelineConfig(_env_file=None)
        assert config.max_bitrate == 96
        assert config.dry_run is True

    def test_path_from_env(self, monkeypatch):
        monkeypatch.setenv("WORK_DIR", "/tmp/test-work")
        config = PipelineConfig(_env_file=None)
        assert config.work_dir == Path("/tmp/test-work")
