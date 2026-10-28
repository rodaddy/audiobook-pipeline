#!/usr/bin/env python3
"""Reject relative imports; package imports must state their absolute path."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = ("src", "tests", "scripts", "_githooks")


def targets(root: Path, named: list[str]) -> list[Path]:
    """Return explicit Python paths or all Python files under enforcement roots."""
    if named:
        return sorted(
            path
            for item in named
            if (path := root / item).is_file() and path.suffix == ".py"
        )
    return sorted(
        path
        for directory in DEFAULT_ROOTS
        if (root / directory).is_dir()
        for path in (root / directory).rglob("*.py")
    )


def main() -> int:
    """Report every relative import and exit nonzero when one exists."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    root = args.root.resolve()
    failures: list[str] = []
    for path in targets(root, args.paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        failures.extend(
            f"{path.relative_to(root)}:{node.lineno}: relative import is forbidden"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        )
    if failures:
        print("Relative imports found:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("Absolute imports OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
