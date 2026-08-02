"""Enforce the config keystone rules that no off-the-shelf linter checks.

Purpose:
    Three rules in ``STANDARDS-python.md`` are load-bearing and invisible to
    ruff and mypy alike, because each is about WHERE something happens rather
    than whether the code is well-formed:

    1. **Only ``config.py`` reads the environment.** A service that reaches for
       ``os.environ`` mid-method can only be tested by mutating global process
       state, and its dependency is invisible in its signature.
    2. **Config layers are loaded by pydantic-settings, not by hand.** A
       hand-written loader silently decides whether env vars beat files. The
       exemplar shipped exactly that bug: files outranked env vars, opposite to
       what its own docstring promised, with nothing logged.
    3. **``secrets/`` holds only secrets.** Non-sensitive layers belong in a
       committed ``config/``, or the shared defaults stop being shared.

    Each check exists because the failure it prevents was observed, not
    imagined. See the standard's ``## config.py -- the keystone``.

Pattern/Convention:
    Runs standalone or from the hook. Same command, same result, either way::

        uv run python _githooks/check_config_compliance.py --check
        uv run python _githooks/check_config_compliance.py --check src/pkg/api.py

    With no paths it walks the configured package. With paths it checks only
    those, which is how ``_githooks/pre-commit`` points it at staged content.

    ``--check`` sets the exit code; without it the script reports and exits 0,
    which is useful when you want the list without failing a script.

Example:
    >>> from pathlib import Path
    >>> _is_config_module(Path("src/audiobook_pipeline/config.py"))
    True
    >>> _is_config_module(Path("src/audiobook_pipeline/services/organize.py"))
    False

See Also:
    - ``_DOCS/STANDARDS-python.md`` ## config.py -- the keystone
    - ``_githooks/check_code_size.py`` -- the sibling check ruff cannot do
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import NamedTuple

#: Modules allowed to read the environment. ``config.py`` is the keystone; the
#: settings module in a src-layout package may also legitimately be named for
#: its package. Nothing else, ever.
CONFIG_MODULE_NAMES = frozenset({"config.py", "settings.py"})

#: Attribute reads that constitute reading the environment.
ENV_READ_ATTRS = frozenset({"environ", "getenv"})

#: Loading a config file by hand instead of declaring it to pydantic-settings.
#: ``json.load`` alone is fine -- reading application DATA is not the offence.
#: The offence is reading a *config layer*, which these path fragments identify.
CONFIG_PATH_HINTS = ("config.json", "config.yaml", "config.yml", "config.toml")


class Violation(NamedTuple):
    """One rule breach, with enough detail to fix it without re-deriving why.

    A ``NamedTuple`` rather than a hand-written class: the four fields are
    immutable data with no behaviour, so writing ``__init__`` by hand would be
    boilerplate the stdlib already generates. This script is a dev tool outside
    the package, so it does not carry the runtime Pydantic dependency the
    application models use -- but the rule that shapes are declared, never
    hand-assembled, is the same one.

    Attributes:
        path: File the violation is in.
        line: 1-indexed line it anchors to.
        rule: Short slug, matching the enforcement table in the standard.
        detail: What is wrong and what to do instead.
    """

    path: Path
    line: int
    rule: str
    detail: str

    def render(self) -> str:
        """Format for terminal output, one violation per two lines."""
        return f"{self.path}:{self.line}  [{self.rule}]\n    {self.detail}"


def _is_config_module(path: Path) -> bool:
    """True when this file is the sanctioned config keystone.

    Args:
        path: Source file being checked.

    Returns:
        Whether the module is permitted to read the environment.
    """
    return path.name in CONFIG_MODULE_NAMES


def _env_reads(tree: ast.Module, path: Path) -> list[Violation]:
    """Find environment reads outside the config keystone.

    ``os.environ``, ``os.environ.get``, ``os.getenv``, and a bare ``getenv``
    imported from ``os`` all count -- the import spelling must not decide
    whether a rule applies.

    Args:
        tree: Parsed module.
        path: Source file, for the message.

    Returns:
        One violation per offending read.
    """
    found: list[Violation] = []
    for raw in ast.walk(tree):
        name = None
        lineno = 0
        if isinstance(raw, ast.Attribute) and raw.attr in ENV_READ_ATTRS:
            name, lineno = raw.attr, raw.lineno
        elif isinstance(raw, ast.Name) and raw.id == "getenv":
            name, lineno = raw.id, raw.lineno
        if name is None:
            continue
        found.append(
            Violation(
                path,
                lineno,
                "env-outside-config",
                f"reads the environment via `{name}`. Only config.py may do "
                f"this. Add a typed field to Settings and pass it in -- a "
                f"dependency in a signature is testable; one in os.environ "
                f"is not.",
            )
        )
    return found


def _hand_rolled_config_load(tree: ast.Module, path: Path) -> list[Violation]:
    """Find config layers being read by hand rather than by pydantic-settings.

    Flags a string literal naming a config file used with ``open`` or a
    ``load``/``read_text`` call. Reading application data is untouched; this
    fires only on paths that look like a config LAYER.

    Args:
        tree: Parsed module.
        path: Source file, for the message.

    Returns:
        One violation per hand-rolled load.
    """
    found: list[Violation] = []
    for raw in ast.walk(tree):
        if not isinstance(raw, ast.Constant):
            continue
        value = raw.value
        if not isinstance(value, str):
            continue
        if not any(hint in value.lower() for hint in CONFIG_PATH_HINTS):
            continue
        found.append(
            Violation(
                path,
                raw.lineno,
                "hand-rolled-config-load",
                f"names a config layer ({value!r}) outside the settings "
                f"source chain. Declare it to JsonConfigSettingsSource in "
                f"settings_customise_sources instead. A hand-written loader "
                f"silently decides whether env vars beat files -- the exemplar "
                f"shipped that bug and it took a live repro to find.",
            )
        )
    return found


def _check_file(path: Path) -> list[Violation]:
    """Run every rule against one source file.

    Args:
        path: Source file to parse and check.

    Returns:
        All violations found. A file that will not parse yields none -- ruff
        and mypy already report syntax errors, and duplicating that here would
        report the same problem twice in different words.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    violations: list[Violation] = []
    if not _is_config_module(path):
        violations.extend(_env_reads(tree, path))
        violations.extend(_hand_rolled_config_load(tree, path))
    return violations


def _check_secrets_layout(root: Path) -> list[Violation]:
    """Verify `secrets/` holds only secrets and their examples.

    A committed non-example JSON file in ``secrets/`` means ordinary settings
    are sitting behind a gitignored directory name, so the shared defaults
    everyone needs are not actually shared.

    Args:
        root: Repository root.

    Returns:
        One violation per misfiled layer.
    """
    secrets = root / "secrets"
    if not secrets.is_dir():
        return []

    found: list[Violation] = []
    for entry in sorted(secrets.glob("*.json")):
        if ".example" in entry.name:
            continue
        found.append(
            Violation(
                entry,
                1,
                "config-in-secrets",
                "a non-example config layer in secrets/. If it holds no "
                "credentials it belongs in the committed config/ directory; "
                "settings hidden behind a gitignored directory stop being "
                "shared. If it DOES hold credentials, it should not be a "
                "tracked filename -- only *.example is committed.",
            )
        )
    return found


def _iter_sources(paths: list[str], root: Path) -> list[Path]:
    """Resolve the files to check.

    Args:
        paths: Explicit paths from the caller. Empty means the whole package.
        root: Repository root, used when walking.

    Returns:
        Python source files, excluding tests -- a test legitimately sets
        environment variables to prove config reads them.
    """
    if paths:
        return [Path(p) for p in paths if p.endswith(".py")]
    return [
        p
        for p in (root / "src").rglob("*.py")
        if "tests" not in p.parts and not p.name.startswith("test_")
    ]


def main(argv: list[str] | None = None) -> int:
    """Report config-compliance violations.

    Args:
        argv: Command-line arguments. ``None`` uses ``sys.argv``.

    Returns:
        Exit code. 1 when ``--check`` was passed and violations exist.
    """
    parser = argparse.ArgumentParser(
        description="Enforce the config keystone rules from STANDARDS-python.md"
    )
    parser.add_argument("paths", nargs="*", help="files to check (default: src/)")
    parser.add_argument("--check", action="store_true", help="exit 1 on any violation")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: cwd)",
    )
    args = parser.parse_args(argv)

    violations: list[Violation] = []
    sources = _iter_sources(args.paths, args.root)
    for source in sources:
        violations.extend(_check_file(source))

    # Layout is a repo-level property, so it is checked once rather than per
    # file -- and only on a full run, since a staged-file run has no opinion
    # about a directory nobody touched.
    if not args.paths:
        violations.extend(_check_secrets_layout(args.root))

    if not violations:
        print(f"config compliance: OK ({len(sources)} files)")
        return 0

    for violation in violations:
        print(violation.render())
    print(f"\n{len(violations)} violation(s). See STANDARDS-python.md ## config.py")
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
