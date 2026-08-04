#!/usr/bin/env python3
"""Validate package docstrings and generate their folder README files."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIN_CHARS = 120
REQUIRED_SECTIONS = ("Key Components:", "Pattern/Convention:", "Example:", "See Also:")
PLACEHOLDERS = (
    "module for",
    "package for",
    "this module",
    "this package",
    "todo",
    "tbd",
    "placeholder",
)
GENERATED_NOTE = (
    "*Auto-generated from `__init__.py` by `_githooks/generate_folder_docs.py`.*"
)


def module_docstring(path: Path) -> str | None:
    """Return the parsed module docstring, failing loudly on invalid source."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return ast.get_docstring(tree, clean=False)


def docstring_problem(docstring: str | None) -> str | None:
    """Return why a docstring is not substantive, or ``None`` when valid."""
    if docstring is None:
        return "no parseable module docstring"
    stripped = docstring.strip()
    if len(stripped) < MIN_CHARS:
        return f"docstring is {len(stripped)} chars (minimum {MIN_CHARS})"
    opening = stripped[:40].lower()
    placeholder = next(
        (item for item in PLACEHOLDERS if opening.startswith(item)), None
    )
    if placeholder is not None:
        return f"placeholder docstring (starts with {placeholder!r})"
    missing = [section for section in REQUIRED_SECTIONS if section not in stripped]
    if missing:
        return f"missing required sections: {', '.join(missing)}"
    return None


def readme_content(package_dir: Path, docstring: str) -> str:
    """Build deterministic README content from one package docstring."""
    title = package_dir.name.replace("_", " ").title()
    return f"# {title}\n\n{docstring.strip()}\n\n---\n{GENERATED_NOTE}\n"


def process_package(
    init_file: Path, check: bool, project_root: Path
) -> tuple[str | None, Path | None]:
    """Validate one package and optionally update its generated README."""
    docstring = module_docstring(init_file)
    problem = docstring_problem(docstring)
    rel = init_file.relative_to(project_root)
    if problem is not None:
        return f"{rel}: {problem}", None
    assert docstring is not None
    readme = init_file.parent / "README.md"
    expected = readme_content(init_file.parent, docstring)
    if readme.exists() and readme.read_text(encoding="utf-8") == expected:
        return None, None
    if check:
        return (
            f"{readme.relative_to(project_root)}: missing or stale generated README",
            None,
        )
    readme.write_text(expected, encoding="utf-8")
    return None, readme.relative_to(project_root)


def main() -> int:
    """Validate all package docs and either check or update generated READMEs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of updating drifted READMEs"
    )
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT, help="repository snapshot root"
    )
    args = parser.parse_args()
    project_root = args.root.resolve()
    package_root = project_root / "src" / "audiobook_pipeline"
    if not package_root.is_dir():
        print(f"ERROR: package root does not exist: {package_root}", file=sys.stderr)
        return 1

    problems: list[str] = []
    updates: list[Path] = []
    init_files = sorted(package_root.rglob("__init__.py"))
    for init_file in init_files:
        problem, updated = process_package(init_file, args.check, project_root)
        if problem is not None:
            problems.append(problem)
        if updated is not None:
            updates.append(updated)

    if problems:
        print(f"{len(problems)} package documentation problem(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    for path in updates:
        print(f"Updated {path}")
    print(
        f"Package docs OK: {len(init_files)} docstrings, {len(updates)} README update(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
