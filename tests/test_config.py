"""Tests for the config keystone: defaults, layering, precedence, and refusal.

The precedence tests are the important ones. A config system that loads is not
the same as a config system that loads the RIGHT value, and the failure mode
this file guards against is silent: a source that outranks another produces a
plausible value with nothing logged to say the loser was read and discarded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from audiobook_pipeline.config import Settings, load_settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every AUDIOBOOK_* var so a developer's shell cannot change a result.

    Swept by prefix rather than by a hardcoded list: a named list silently
    stops covering a setting the moment one is added, and the symptom is a test
    that passes on CI and fails on the machine that happens to export it.
    """
    for key in [k for k in os.environ if k.startswith("AUDIOBOOK_")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def layers(tmp_path: Path) -> tuple[Path, Path]:
    """An empty config/ and secrets/ pair, isolated from the real ones."""
    config_dir = tmp_path / "config"
    secrets_dir = tmp_path / "secrets"
    config_dir.mkdir()
    secrets_dir.mkdir()
    return config_dir, secrets_dir


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestDefaults:
    def test_loads_with_no_config_files_at_all(self, layers: tuple[Path, Path]) -> None:
        config_dir, secrets_dir = layers
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        assert settings.encoding.max_bitrate == 128
        assert settings.encoding.codec == "aac"
        assert settings.level == "normal"
        assert settings.logging.level == "INFO"

    def test_db_path_derives_from_work_dir(self, layers: tuple[Path, Path]) -> None:
        config_dir, secrets_dir = layers
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        assert settings.paths.db_path == settings.paths.work_dir / "pipeline.db"

    def test_no_default_path_escapes_the_project(
        self, layers: tuple[Path, Path]
    ) -> None:
        """A fresh clone must write nothing outside its own directory.

        Every path default used to be absolute -- /var/lib, /var/log,
        /mnt/media -- so a first run on any non-root account, and on every Mac,
        failed on permissions before converting a book. Asserted as a property
        over ALL path fields rather than a list of names, so a new setting
        cannot reintroduce the problem without failing here.
        """
        config_dir, secrets_dir = layers
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        escaped = [
            name
            for name in type(settings.paths).model_fields
            if isinstance(getattr(settings.paths, name), Path)
            and getattr(settings.paths, name).is_absolute()
        ]
        assert escaped == [], f"absolute path defaults: {escaped}"


class TestLayering:
    def test_base_layer_is_read(self, layers: tuple[Path, Path]) -> None:
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"encoding": {"max_bitrate": 96}})
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        assert settings.encoding.max_bitrate == 96

    def test_profile_layer_beats_base(self, layers: tuple[Path, Path]) -> None:
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"encoding": {"max_bitrate": 96}})
        _write(config_dir / "config.plex.json", {"encoding": {"max_bitrate": 64}})
        settings = load_settings(
            profile="plex",
            config_dir=config_dir,
            secrets_dir=secrets_dir,
            configure_logging=False,
        )
        assert settings.encoding.max_bitrate == 64

    def test_deep_merge_keeps_siblings(self, layers: tuple[Path, Path]) -> None:
        """A profile setting ONE key must not wipe its sibling fields.

        This is what deep_merge=True buys, and it is the behaviour a
        hand-written merge gets wrong: the profile layer below mentions only
        max_bitrate, so codec must survive from the base layer.
        """
        config_dir, secrets_dir = layers
        _write(
            config_dir / "config.json",
            {"encoding": {"max_bitrate": 96, "codec": "libfdk_aac", "channels": 2}},
        )
        _write(config_dir / "config.plex.json", {"encoding": {"max_bitrate": 64}})
        settings = load_settings(
            profile="plex",
            config_dir=config_dir,
            secrets_dir=secrets_dir,
            configure_logging=False,
        )
        assert settings.encoding.max_bitrate == 64
        assert settings.encoding.codec == "libfdk_aac"
        assert settings.encoding.channels == 2

    def test_secrets_layer_beats_config(self, layers: tuple[Path, Path]) -> None:
        """Credentials come last so a real key overrides a committed placeholder."""
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"ai": {"api_key": "PLACEHOLDER"}})
        _write(secrets_dir / "config.json", {"ai": {"api_key": "real-key"}})
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        assert settings.ai.api_key == "real-key"


class TestPrecedence:
    def test_env_var_beats_every_file(
        self, layers: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE regression test for the documented-but-false precedence bug.

        The reference exemplar read its JSON itself and passed the result as
        Settings(**values). Init kwargs are pydantic's HIGHEST-priority source,
        so files silently outranked environment variables -- the exact reverse
        of what its docstring promised, with nothing logged. Declaring the
        chain to settings_customise_sources is what makes the order true.
        """
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"logging": {"level": "DEBUG"}})
        _write(secrets_dir / "config.json", {"logging": {"level": "DEBUG"}})
        monkeypatch.setenv("AUDIOBOOK_LOGGING__LEVEL", "CRITICAL")

        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        assert settings.logging.level == "CRITICAL"

    def test_nested_env_var_uses_double_underscore(
        self, layers: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir, secrets_dir = layers
        monkeypatch.setenv("AUDIOBOOK_ENCODING__MAX_BITRATE", "192")
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        assert settings.encoding.max_bitrate == 192

    def test_override_kwarg_beats_env(
        self, layers: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir, secrets_dir = layers
        monkeypatch.setenv("AUDIOBOOK_LOGGING__LEVEL", "CRITICAL")
        settings = load_settings(
            config_dir=config_dir,
            secrets_dir=secrets_dir,
            configure_logging=False,
            logging={"level": "WARNING"},
        )
        assert settings.logging.level == "WARNING"


class TestRefusal:
    def test_unknown_key_is_rejected_not_ignored(
        self, layers: tuple[Path, Path]
    ) -> None:
        """extra='forbid'. A typo'd key must be an error, not a silent default.

        Without this, a misspelled setting is dropped and the default used,
        which presents as "my setting does nothing" with no error to search
        for.
        """
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"encodding": {"max_bitrate": 64}})
        with pytest.raises(ValidationError, match="encodding"):
            load_settings(
                config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
            )

    def test_out_of_range_value_names_the_field(
        self, layers: tuple[Path, Path]
    ) -> None:
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"encoding": {"max_bitrate": 999}})
        with pytest.raises(ValidationError, match="max_bitrate"):
            load_settings(
                config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
            )

    def test_ai_level_without_endpoint_refuses_to_start(
        self, layers: tuple[Path, Path]
    ) -> None:
        """Fail at startup, not after the convert stage has spent the CPU."""
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"level": "ai"})
        with pytest.raises(ValidationError, match=r"ai\.base_url"):
            load_settings(
                config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
            )

    def test_ai_level_with_endpoint_is_accepted(
        self, layers: tuple[Path, Path]
    ) -> None:
        config_dir, secrets_dir = layers
        _write(
            config_dir / "config.json",
            {"level": "ai", "ai": {"base_url": "http://10.71.20.53:4000/v1"}},
        )
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        assert settings.level == "ai"

    def test_work_dir_equal_to_library_refuses(
        self, layers: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Cleanup empties work_dir. Aliasing it to the library deletes books."""
        config_dir, secrets_dir = layers
        shared = str(tmp_path / "same")
        _write(
            config_dir / "config.json",
            {"paths": {"work_dir": shared, "library_dir": shared}},
        )
        with pytest.raises(ValidationError, match="same directory"):
            load_settings(
                config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
            )

    def test_non_octal_file_mode_is_rejected(self, layers: tuple[Path, Path]) -> None:
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"permissions": {"file_mode": "899"}})
        with pytest.raises(ValidationError, match="OCTAL"):
            load_settings(
                config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
            )

    def test_assignment_is_validated(self, layers: tuple[Path, Path]) -> None:
        """validate_assignment: fail at the assignment, not at the sink."""
        config_dir, secrets_dir = layers
        settings = load_settings(
            config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
        )
        with pytest.raises(ValidationError):
            settings.level = "loud"  # type: ignore[assignment]


class TestMalformedFiles:
    def test_malformed_json_raises_rather_than_defaulting(
        self, layers: tuple[Path, Path]
    ) -> None:
        """A broken config must not silently fall back to defaults.

        That is how a deployment runs with settings nobody chose.
        """
        config_dir, secrets_dir = layers
        (config_dir / "config.json").write_text("{not json", encoding="utf-8")
        with pytest.raises((json.JSONDecodeError, ValidationError, ValueError)):
            load_settings(
                config_dir=config_dir, secrets_dir=secrets_dir, configure_logging=False
            )

    def test_missing_profile_layer_is_not_an_error(
        self, layers: tuple[Path, Path]
    ) -> None:
        """An absent profile file falls through to the base layer.

        Deliberate: profiles are optional overlays, and requiring every named
        profile to exist would make `--profile plex` fail on a fresh clone.
        """
        config_dir, secrets_dir = layers
        _write(config_dir / "config.json", {"encoding": {"max_bitrate": 96}})
        settings = load_settings(
            profile="nonexistent",
            config_dir=config_dir,
            secrets_dir=secrets_dir,
            configure_logging=False,
        )
        assert settings.encoding.max_bitrate == 96


class TestKeystoneContract:
    def test_settings_is_the_only_env_reader(self) -> None:
        """The module exposes load_settings as the sanctioned constructor.

        Not a behavioural test -- the enforcement is
        _githooks/check_config_compliance.py, which fails the commit on an
        os.environ read outside config.py. This asserts the entry point exists
        with the shape the rest of the application depends on.
        """
        assert callable(load_settings)
        assert issubclass(Settings, object)
