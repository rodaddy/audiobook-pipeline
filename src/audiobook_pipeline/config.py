"""Configuration. Runs first, validates everything, configures logging, gets passed down.

THIS MODULE IS THE KEYSTONE
    Nothing else in this application reads an environment variable, opens a
    config file, or configures a logging sink. Every component receives what it
    needs through its constructor. That single rule is what makes the rest
    testable: a service whose dependencies arrive as arguments can be handed
    fakes, while a service that reaches for ``os.environ`` in the middle of a
    method can only be tested by mutating global process state.

    The rule is enforced, not merely stated --
    ``_githooks/check_config_compliance.py`` fails the commit on an
    ``os.environ`` read outside this file. The pre-rewrite code broke it in
    ``cli_audit.py``, which pulled ``PLEX_TOKEN`` straight from the environment
    three lines below a comment explaining why that was wrong.

    It is also the one file exempt from the 500-line ceiling (see
    _DOCS/STANDARDS-python.md ## LAW: 500 lines maximum per file). It holds the
    complete typed surface of everything the application can be told to do, and
    splitting it scatters the one place a reader goes to find that out.

CONFIGURATION SOURCES, IN PRECEDENCE ORDER
    Highest wins. This order is stated here for the reader, but it is TRUE
    because ``settings_customise_sources`` below declares it to
    pydantic-settings -- not because this docstring says so:

    1. Explicit keyword arguments to ``Settings(...)``   -- tests only
    2. Environment variables (``AUDIOBOOK_`` prefix, ``__`` nesting)
    3. ``secrets/config.json``          -- credentials, gitignored
    4. ``config/config.{profile}.json`` -- the chosen profile layer
    5. ``config/config.json``           -- committed shared defaults
    6. Field defaults declared below

    Nested values use a double underscore: ``AUDIOBOOK_LOGGING__LEVEL=DEBUG``
    sets ``settings.logging.level``. A single underscore would be ambiguous the
    moment a field name contains one.

    The distinction between enforcing that order and asserting it is not
    academic. The reference exemplar previously read its JSON files itself and
    passed the result as keyword arguments to ``Settings(...)``. Init kwargs are
    pydantic's HIGHEST-priority source, so the files silently outranked
    environment variables -- the exact reverse of what its docstring promised,
    with nothing logged to show a variable had been read and discarded.

WHY config/ AND secrets/ ARE SEPARATE DIRECTORIES
    ``config/`` is committed and diffable: paths, bitrates, regions,
    thresholds, the named profiles. ``secrets/`` is gitignored and holds only
    credentials. Putting ordinary settings behind a gitignored directory name
    is how shared defaults stop being shared -- a reader who sees
    ``secrets/config.json`` reasonably concludes it cannot be committed.

SELF-CONTAINED BY DEFAULT
    Every path defaults under ``data/``, relative to the project. Clone the
    repo, run it, and nothing is written outside that folder. The pre-rewrite
    code defaulted all eleven paths to ``/var/lib``, ``/var/log``, and
    ``/mnt/media``, so a first run on any non-root account -- and on every Mac
    -- failed on permissions before converting a single book. System paths are
    a DEPLOYMENT choice (``config/config.server.json``), not a sensible default
    for someone trying the tool.

Key Components:
    - Settings: the root object. Built once, at startup, passed down.
    - LoggingSettings: sinks, levels, rotation. Consumed by utils.logging_config.
    - PathSettings / EncodingSettings / MetadataSettings / AiSettings: sections.
    - load_settings: the sanctioned constructor. Use this, not ``Settings()``.

Pattern/Convention:
    Every entry point starts the same way::

        settings = load_settings(profile="plex")   # reads, validates, logs
        service = Organizer(settings.paths)        # inject the section

    Never ``import config`` deep inside a service to fetch a value. If a service
    needs something, it is a constructor parameter -- that is what makes it
    visible in the signature and replaceable in a test.

Example:
    >>> settings = load_settings(configure_logging=False)
    >>> settings.encoding.max_bitrate
    128

See Also:
    - audiobook_pipeline.utils.logging_config: the only consumer of LoggingSettings
    - _DOCS/STANDARDS-python.md ## config.py -- the keystone
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from audiobook_pipeline.models.stage import PipelineLevel, PipelineMode

# --------------------------------------------------------------------------
# Constants. Named at module level, never inline in a field default -- a magic
# number in a default is invisible to anyone reading the class.
# --------------------------------------------------------------------------

#: Project root, derived from this file: src/audiobook_pipeline/config.py -> up
#: three. Deriving beats hardcoding -- it survives the repo being cloned
#: anywhere, which is the whole point of the self-contained defaults.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Everything the application writes lives under here by default.
DEFAULT_DATA_DIR = Path("data")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


class PathSettings(BaseModel):
    """Where the pipeline reads, writes, and files finished books.

    Every default is RELATIVE, and a property test in ``tests/test_config.py``
    asserts that over all fields rather than by name, so a new path setting
    cannot reintroduce an absolute default without failing.
    """

    @field_validator("*", mode="after")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        """Expand a leading ``~`` on EVERY path field.

        Applied with ``"*"`` rather than per field so a new path setting is
        covered the day it is added, which is the same reason the relative
        default test asserts over all fields instead of by name.

        Without this, ``Path`` keeps the tilde as a literal component: a user
        writing ``~/Audiobooks`` -- the natural spelling, and the one the
        example profiles use -- gets a directory actually named "~" created in
        the working directory, with the library filed inside it. Nothing
        raises, so the only symptom is that the books are somewhere nobody
        thinks to look.
        """
        return value.expanduser()

    data_dir: Path = DEFAULT_DATA_DIR

    work_dir: Path = DEFAULT_DATA_DIR / "work"
    output_dir: Path = DEFAULT_DATA_DIR / "output"
    log_dir: Path = DEFAULT_DATA_DIR / "logs"
    archive_dir: Path = DEFAULT_DATA_DIR / "archive"
    lock_dir: Path = DEFAULT_DATA_DIR / "locks"

    #: Where finished books are filed as Author/Series/Title. Defaults inside
    #: the project so a first run is safe; point it at the real library when
    #: ready. Named library_dir, not nfs_output_dir -- it need not be NFS, and
    #: the old name described one deployment's transport as if it were the
    #: concept.
    library_dir: Path = DEFAULT_DATA_DIR / "library"

    # Watch-folder workflow.
    incoming_dir: Path = DEFAULT_DATA_DIR / "incoming"
    queue_dir: Path = DEFAULT_DATA_DIR / "queue"
    processing_dir: Path = DEFAULT_DATA_DIR / "processing"
    completed_dir: Path = DEFAULT_DATA_DIR / "completed"
    failed_dir: Path = DEFAULT_DATA_DIR / "failed"

    @property
    def db_path(self) -> Path:
        """Path to the SQLite pipeline database."""
        return self.work_dir / "pipeline.db"

    def ensure_dirs(self) -> None:
        """Create the directories the pipeline writes to.

        Not every path -- only the ones a run will write. Creating
        ``library_dir`` here would silently produce an empty folder at whatever
        the default is when someone has misconfigured their real library, which
        reads as success.
        """
        for directory in (
            self.work_dir,
            self.output_dir,
            self.log_dir,
            self.archive_dir,
            self.lock_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


class LoggingSettings(BaseModel):
    """Logging sinks and levels. The sole input to utils.logging_config.setup.

    Defaults are the safe desktop shape: INFO to the console, a rotating file,
    and no JSON. An unattended server turns the JSON sink on; nothing has to be
    turned *off* to be safe.
    """

    level: LogLevel = "INFO"

    #: Plain-text rotating sink -- the one a human tails.
    file_sink: bool = True

    #: Structured JSON, one object per line -- the one a log platform ingests.
    #: Off by default: a desktop user converting a book does not need a second
    #: log file, but "which books failed at the convert stage last week" should
    #: not require re-parsing human-formatted lines with a regex.
    json_sink: bool = False

    #: loguru size or time spec.
    rotation: str = "10 MB"
    retention: str = "30 days"

    #: backtrace+diagnose. Shows local variable VALUES in tracebacks, which is
    #: excellent locally and a disclosure risk anywhere a frame may hold an API
    #: key. Off by default; turned on explicitly when debugging.
    verbose_tracebacks: bool = False


class EncodingSettings(BaseModel):
    """Audio encoding parameters for the convert stage."""

    #: kbps. Audiobook speech at 128 mono is transparent; higher is wasted
    #: bytes on a spoken-word source.
    max_bitrate: int = Field(default=128, ge=32, le=320)
    channels: int = Field(default=1, ge=1, le=2)
    codec: str = "aac"

    #: 0 = auto, derived from CPU count at runtime.
    max_parallel_converts: int = Field(default=0, ge=0, le=64)
    cpu_ceiling: float = Field(default=80.0, gt=0, le=100)

    #: Per-worker FFmpeg threads. Zero preserves FFmpeg's automatic choice.
    threads: int = Field(default=0, ge=0, le=256)


class PermissionSettings(BaseModel):
    """POSIX ownership and mode applied to output files.

    Octal strings rather than ints: ``644`` as an integer is 644 decimal, and
    the resulting mode is silently wrong rather than an error.
    """

    file_owner: str = ""
    file_mode: str = "644"
    dir_mode: str = "755"

    @field_validator("file_mode", "dir_mode")
    @classmethod
    def _valid_octal(cls, value: str) -> str:
        """Reject a mode that is not three or four octal digits."""
        if not value.isdigit() or not 3 <= len(value) <= 4:
            msg = (
                f"mode must be 3-4 octal digits (e.g. '644'); got {value!r}. "
                f"ACTION REQUIRED: set a valid octal mode."
            )
            raise ValueError(msg)
        if any(digit not in "01234567" for digit in value):
            msg = (
                f"mode must be OCTAL; {value!r} contains a digit above 7. "
                f"ACTION REQUIRED: 8 and 9 are not valid in a file mode."
            )
            raise ValueError(msg)
        return value


class MetadataSettings(BaseModel):
    """Where book metadata comes from and how strictly it is matched."""

    source: str = "audible"
    audible_region: str = "com"
    audnexus_region: str = "us"

    audnexus_cache_dir: str = ""
    audnexus_cache_days: int = Field(default=30, ge=0)

    #: Percent. How far a fetched chapter table may diverge from the actual
    #: audio duration before it is rejected as the wrong edition -- an
    #: abridged release has the same ASIN shape and a very different runtime.
    chapter_duration_tolerance: int = Field(default=5, ge=0, le=100)

    #: rapidfuzz score below which an Audible search result is not a match.
    asin_search_threshold: int = Field(default=65, ge=0, le=100)

    skip: bool = False
    force: bool = False


class AiSettings(BaseModel):
    """LLM-assisted disambiguation. Inert unless the pipeline level enables it.

    Named PIPELINE_LLM_* in the environment rather than OPENAI_* so a global
    OPENAI_API_KEY on the developer's machine cannot silently redirect this
    application's traffic or bill the wrong account.
    """

    base_url: str = ""
    api_key: str = ""
    model: str = "haiku"

    #: Run every book through the LLM rather than only ambiguous ones.
    all_books: bool = False

    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class AutomationSettings(BaseModel):
    """Watch-folder and retry behaviour for unattended runs."""

    #: Seconds a file's size must hold steady before it is considered fully
    #: written. A file still being copied is not ready to convert, and the
    #: failure looks like a corrupt source rather than a race.
    stability_threshold: int = Field(default=120, ge=0)

    poll_interval_seconds: float = Field(default=30.0, gt=0, le=3600)
    watch_mode: PipelineMode = PipelineMode.CONVERT

    max_retries: int = Field(default=3, ge=0, le=10)
    archive_retention_days: int = Field(default=90, ge=0)

    failure_webhook_url: str = ""
    failure_email: str = ""


# --------------------------------------------------------------------------
# Root
# --------------------------------------------------------------------------


class Settings(BaseSettings):
    """Root configuration for the whole pipeline.

    Built once at startup by ``load_settings`` and passed down explicitly.
    Constructing this directly skips logging setup, so prefer ``load_settings``
    everywhere except tests that specifically want a bare object.
    """

    model_config = SettingsConfigDict(
        env_prefix="AUDIOBOOK_",
        env_nested_delimiter="__",
        # Reject unknown keys instead of ignoring them. A typo'd key in a
        # config file is otherwise silently dropped and the default used, which
        # presents as "my setting does nothing" with no error to search for.
        extra="forbid",
        # Validate on assignment too, so `settings.logging.level = "LOUD"`
        # fails at the assignment rather than at the sink.
        validate_assignment=True,
    )

    # Where the JSON layers live and which profile to load. Set by
    # `load_settings` before construction, read by `settings_customise_sources`.
    #
    # ClassVar, so pydantic treats these as plain class attributes rather than
    # model fields -- without that annotation they would become settable config
    # keys, and `extra="forbid"` would start rejecting good files that happen
    # not to mention them.
    _config_dir: ClassVar[Path] = PROJECT_ROOT / "config"
    _secrets_dir: ClassVar[Path] = PROJECT_ROOT / "secrets"
    _profile: ClassVar[str] = "default"

    #: Which named profile was loaded. Recorded so a log line can say it.
    profile: str = "default"

    #: Intelligence tier. Controls whether the AI stages run at all.
    level: PipelineLevel = PipelineLevel.NORMAL

    dry_run: bool = False
    force: bool = False
    verbose: bool = False
    cleanup_work_dir: bool = True

    paths: PathSettings = Field(default_factory=PathSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    encoding: EncodingSettings = Field(default_factory=EncodingSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    metadata: MetadataSettings = Field(default_factory=MetadataSettings)
    ai: AiSettings = Field(default_factory=AiSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Declare the source chain, highest precedence first.

        This is the mechanism behind the precedence order in the module
        docstring, and the reason that order is true rather than merely
        claimed. pydantic-settings walks the returned tuple in order and takes
        the first source that supplies each value.

        Passing a LIST of files with ``deep_merge=True`` also removes the need
        for a hand-written recursive merge: a profile layer that sets only
        ``logging.level`` leaves the sibling logging fields intact.

        Args:
            settings_cls: The settings class being built, passed through to the
                JSON source, which needs it to read ``model_config``.
            init_settings: Keyword arguments passed to ``Settings(...)``.
            env_settings: ``AUDIOBOOK_``-prefixed environment variables.
            dotenv_settings: ``.env`` file. Deliberately unused -- the layers
                live in ``config/`` and ``secrets/``, and two competing file
                conventions is one too many.
            file_secret_settings: Docker-style secrets directory. Unused; the
                JSON layers cover the same need explicitly.

        Returns:
            Sources in descending precedence.
        """
        config_dir = cls._config_dir
        secrets_dir = cls._secrets_dir
        profile = cls._profile

        return (
            init_settings,
            env_settings,
            # All layers as ONE source, listed lowest-first: `deep_merge`
            # applies each file over the previous, so credentials must come
            # last to win over a placeholder in a committed file.
            JsonConfigSettingsSource(
                settings_cls,
                json_file=[
                    config_dir / "config.json",
                    config_dir / f"config.{profile}.json",
                    secrets_dir / "config.json",
                ],
                deep_merge=True,
            ),
        )

    @model_validator(mode="after")
    def _check_cross_field_invariants(self) -> Settings:
        """Validate rules that span more than one field.

        Single-field rules belong on the field itself; these need two or more
        values and therefore run after the whole object is built.
        """
        # An AI tier with no endpoint configured starts, runs, and fails at the
        # first book -- after the convert stage has already spent minutes of
        # CPU. Refuse at startup instead.
        if (
            self.level in (PipelineLevel.AI, PipelineLevel.FULL)
            and not self.ai.base_url
        ):
            msg = (
                f"level={self.level!r} requires ai.base_url, which is empty. "
                f"ACTION REQUIRED: set AUDIOBOOK_AI__BASE_URL, or use "
                f"level='normal' which never calls an LLM."
            )
            raise ValueError(msg)

        # The work directory holds in-progress conversions; the library holds
        # finished books. Pointing them at the same place means cleanup deletes
        # the library.
        if self.paths.work_dir.resolve() == self.paths.library_dir.resolve():
            msg = (
                f"paths.work_dir and paths.library_dir are the same directory "
                f"({self.paths.work_dir}). Cleanup empties the work directory, "
                f"so this would delete the library. "
                f"ACTION REQUIRED: point them at different paths."
            )
            raise ValueError(msg)

        return self


def load_settings(
    *,
    profile: str = "default",
    config_dir: Path | None = None,
    secrets_dir: Path | None = None,
    configure_logging: bool = True,
    # `Any` is correct here and narrowing it would be a lie. These are
    # arbitrary Settings field overrides for tests, so the accepted type is
    # genuinely "whatever Settings accepts", which is not expressible without
    # duplicating the model. Pydantic validates every one at construction, so
    # nothing untyped survives past this call.
    **overrides: Any,
) -> Settings:
    """Build the application's settings and configure logging.

    The sanctioned entry point. Every application's ``main`` calls this exactly
    once, before constructing anything else.

    Args:
        profile: Which named layer to load from ``config/config.{profile}.json``.
        config_dir: Where the committed layers live. Defaults to ``config/`` at
            the project root; overridable so tests never read the real files.
        secrets_dir: Where credentials live. Defaults to ``secrets/``.
        configure_logging: Set up logging sinks as part of loading. True in
            every real entry point. Tests that build settings to inspect values
            pass False to avoid reconfiguring sinks for the whole session.
        **overrides: Highest-precedence values, for tests.

    Returns:
        Validated settings.

    Raises:
        ValidationError: A config file is malformed, a value is out of range,
            or a cross-field rule failed. Deliberately not caught -- an
            application whose configuration is wrong must not start, because
            every later failure would be a confusing symptom of this one cause.

    Example:
        >>> settings = load_settings(configure_logging=False)
        >>> settings.paths.work_dir
        PosixPath('data/work')
    """
    # Bind the layers before construction. `settings_customise_sources` is a
    # classmethod and reads these; this function is the only sanctioned place
    # allowed to create that coupling.
    Settings._config_dir = (
        config_dir if config_dir is not None else PROJECT_ROOT / "config"
    )
    Settings._secrets_dir = (
        secrets_dir if secrets_dir is not None else PROJECT_ROOT / "secrets"
    )
    Settings._profile = profile

    # `profile` is passed explicitly so the caller's choice outranks any file
    # naming a different one -- a file claiming otherwise is stale.
    settings = Settings(profile=profile, **overrides)

    if configure_logging:
        from audiobook_pipeline.utils.logging_config import setup as setup_logging

        setup_logging(settings.logging, log_dir=settings.paths.log_dir)

    return settings
