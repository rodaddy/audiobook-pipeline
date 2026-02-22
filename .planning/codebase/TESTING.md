# Testing Patterns

**Analysis Date:** 2026-02-21

## Test Framework

**Runner:**
- pytest >= 8.0
- No pytest config in `pyproject.toml` (no `[tool.pytest.ini_options]` section)
- No `conftest.py` files (fixtures are defined per-test-file)

**Assertion Library:**
- Built-in `assert` statements (pytest rewrites)
- `pytest.raises` for exception testing

**Mocking:**
- `unittest.mock.patch` (standard library)
- `pytest-httpx` >= 0.30 (available as dev dependency for HTTP mocking, not yet used in existing tests)

**Run Commands:**
```bash
uv run pytest tests/ -v          # Run all tests verbose
uv run pytest tests/ -v -x       # Stop on first failure
uv run pytest tests/test_config.py  # Single module
```

## Test File Organization

**Location:**
- Separate `tests/` directory at project root (not co-located)
- Test subdirectories mirror source package structure

**Naming:**
- `test_{module_name}.py` -- mirrors source module name exactly
- Test classes: `Test{FeatureName}` -- grouped by logical feature, not by function

**Structure:**
```
tests/
  __init__.py              # Package docstring
  test_config.py           # Tests for src/audiobook_pipeline/config.py
  test_manifest.py         # Tests for src/audiobook_pipeline/manifest.py
  test_sanitize.py         # Tests for src/audiobook_pipeline/sanitize.py
  test_models.py           # Tests for src/audiobook_pipeline/models.py
  test_errors.py           # Tests for src/audiobook_pipeline/errors.py
  test_ffprobe.py          # Tests for src/audiobook_pipeline/ffprobe.py
  test_concurrency.py      # Tests for src/audiobook_pipeline/concurrency.py
  test_cli.py              # Tests for src/audiobook_pipeline/cli.py
  test_logging.py          # Tests for logging setup in config.py
  test_api/                # Tests for src/audiobook_pipeline/api/
    __init__.py
  test_ops/                # Tests for src/audiobook_pipeline/ops/
    __init__.py
  test_stages/             # Tests for src/audiobook_pipeline/stages/
    __init__.py
```

## Test Structure

**Suite Organization:**
```python
"""Tests for {module}.py -- brief description of what's tested."""

import pytest
from audiobook_pipeline.{module} import {ClassOrFunction}


@pytest.fixture
def thing(tmp_path):
    """Create a test instance."""
    return Thing(tmp_path / "subdir")


class TestFeatureGroup:
    def test_specific_behavior(self):
        result = do_thing()
        assert result == expected

    def test_edge_case(self):
        assert do_thing("edge") == "handled"
```

**Key patterns:**
- Group related tests into classes: `TestCreate`, `TestRead`, `TestUpdate`, `TestSetStage`
- Test method names describe the behavior: `test_default_values`, `test_env_var_override`, `test_missing_file`
- No `setUp`/`tearDown` -- use pytest fixtures instead (except `test_logging.py` which uses `setup_method`)
- Tests are independent -- no shared mutable state between test methods

## Fixtures

**Common Fixture Patterns:**

**Autouse fixtures for environment isolation:**
```python
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove pipeline env vars so defaults tests see actual defaults."""
    for var in _CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
```
Used in: `tests/test_config.py`, `tests/test_cli.py`, `tests/test_logging.py`

**tmp_path for filesystem tests:**
```python
@pytest.fixture
def manifest(tmp_path):
    return Manifest(tmp_path / "manifests")
```
Used in: `tests/test_manifest.py`, `tests/test_sanitize.py`, `tests/test_concurrency.py`

**Composed fixtures (fixture depending on fixture):**
```python
@pytest.fixture
def book(manifest):
    """Create a manifest and return (manifest, book_hash)."""
    manifest.create("abc123", "/input/book", PipelineMode.CONVERT)
    return manifest, "abc123"
```
Used in: `tests/test_manifest.py`

**Config isolation (CRITICAL):**
```python
# Always pass _env_file=None to prevent loading the project .env
config = PipelineConfig(_env_file=None)

# For CLI tests, also mock _find_config_file
@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    monkeypatch.setattr("audiobook_pipeline.cli._find_config_file", lambda: None)
```

## Mocking

**Framework:** `unittest.mock.patch` (decorator style)

**Subprocess mocking pattern (ffprobe):**
```python
def _mock_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )

class TestGetDuration:
    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_parses_float(self, mock_run):
        mock_run.return_value = _mock_result("123.456\n")
        assert get_duration(Path("test.mp3")) == 123.456
```
Used in: `tests/test_ffprobe.py`

**Class-level mocking (CLI runner):**
```python
class TestModeAutoDetect:
    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_directory_defaults_to_convert(self, mock_runner_cls, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, [str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        mode_arg = mock_runner_cls.call_args.kwargs.get("mode")
        assert mode_arg == PipelineMode.CONVERT
```
Used in: `tests/test_cli.py`

**Disk usage mocking:**
```python
def test_insufficient_space(self, tmp_path):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"x" * 1000)
    fake_usage = type("Usage", (), {"free": 100, "total": 1000, "used": 900})()
    with patch("audiobook_pipeline.concurrency.shutil.disk_usage", return_value=fake_usage):
        assert check_disk_space(source, tmp_path) is False
```
Used in: `tests/test_concurrency.py`

**What to Mock:**
- External subprocesses (`ffprobe`, `ffmpeg`) -- always mock via `_run_ffprobe` or `subprocess.run`
- System resources (`shutil.disk_usage`) -- mock for edge case testing
- Pipeline orchestration classes (`PipelineRunner`) -- mock in CLI tests to test arg parsing
- Config file discovery (`_find_config_file`) -- mock to prevent .env leakage

**What NOT to Mock:**
- Pure logic functions (`sanitize_filename`, `parse_path`, `score_results`)
- Enum/constant definitions
- Manifest read/write (use real `tmp_path` filesystem)
- Config construction (use real `PipelineConfig` with `_env_file=None`)

## Fixtures and Factories

**Test Data:**
```python
# Inline test data -- no separate fixtures directory
f = tmp_path / "test.mp3"
f.write_bytes(b"audio data")

# Directory structures built in-test
audio_dir = tmp_path / "book"
audio_dir.mkdir()
(audio_dir / "ch1.mp3").write_bytes(b"ch1")
(audio_dir / "ch2.flac").write_bytes(b"ch2")
(audio_dir / "cover.jpg").write_bytes(b"img")  # not audio
```

**Location:**
- No separate fixtures directory
- Test data created inline using `tmp_path`
- Helper functions defined at module level: `_mock_result()` in `tests/test_ffprobe.py`

## Coverage

**Requirements:** None enforced (no coverage config or CI pipeline detected)

**View Coverage:**
```bash
uv run pytest tests/ --cov=audiobook_pipeline --cov-report=term-missing
```

## Test Types

**Unit Tests:**
- All existing tests are unit tests
- Test individual functions/methods in isolation
- Use `tmp_path` for filesystem tests, `patch` for subprocess tests
- Fast execution -- no network calls, no real ffprobe

**Integration Tests:**
- Not present. `tests/test_api/`, `tests/test_ops/`, `tests/test_stages/` directories exist but are empty (only `__init__.py`)

**E2E Tests:**
- Not present
- CLI tests (`tests/test_cli.py`) test argument parsing but mock the runner

## Common Patterns

**Parametrized Testing:**
```python
@pytest.mark.parametrize("code", [2, 3])
def test_permanent_codes(self, code: int):
    assert categorize_exit_code(code) == ErrorCategory.PERMANENT

@pytest.mark.parametrize("code", [1, 4, 127, 255])
def test_transient_codes(self, code: int):
    assert categorize_exit_code(code) == ErrorCategory.TRANSIENT
```
Used in: `tests/test_errors.py`

**Error Testing:**
```python
def test_update_missing_raises(self, manifest):
    with pytest.raises(ManifestError):
        manifest.update("nope", {"status": "x"})

def test_second_lock_raises(self, tmp_path):
    lock_dir = tmp_path / "locks"
    fh1 = acquire_global_lock(lock_dir)
    with pytest.raises(LockError, match="Another pipeline instance"):
        acquire_global_lock(lock_dir)
    fh1.close()
```

**CLI Testing (Click):**
```python
from click.testing import CliRunner

def test_help_flag(self):
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--mode" in result.output
```

**Environment Variable Testing:**
```python
def test_env_var_override(self, monkeypatch):
    monkeypatch.setenv("MAX_BITRATE", "96")
    monkeypatch.setenv("DRY_RUN", "true")
    config = PipelineConfig(_env_file=None)
    assert config.max_bitrate == 96
    assert config.dry_run is True
```

**Assertion on error message content:**
```python
result = runner.invoke(main, [str(txt)])
assert result.exit_code != 0
assert "Cannot auto-detect mode" in result.output
```

## Adding New Tests

**For a new module `src/audiobook_pipeline/foo.py`:**
1. Create `tests/test_foo.py`
2. Import from `audiobook_pipeline.foo` (installed package, not relative)
3. Group tests into classes by feature: `TestCreate`, `TestValidation`, etc.
4. Use `tmp_path` for filesystem needs
5. Mock external dependencies (`subprocess`, HTTP, AI client)
6. Add `_env_file=None` when constructing `PipelineConfig`
7. Clean relevant env vars with `monkeypatch.delenv()` in autouse fixture

**For a new subpackage module `src/audiobook_pipeline/ops/bar.py`:**
1. Create `tests/test_ops/test_bar.py`
2. Ensure `tests/test_ops/__init__.py` exists (it does)
3. Follow same patterns as above

---

*Testing analysis: 2026-02-21*
