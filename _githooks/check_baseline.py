#!/usr/bin/env python3
"""Reject new Ruff shape or code-size violations against a tracked baseline."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "_githooks" / "baseline.json"
BASELINED_RULES = frozenset({"C901", "PLR0912", "PLR0913", "PLR0915", "PLR1702"})


def function_at(path: Path, line: int) -> str:
    """Return the smallest qualified function enclosing a finding line."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    matches: list[tuple[int, str]] = []

    def visit(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        name = getattr(node, "name", None)
        nested = parents + ((name,) if isinstance(name, str) else ())
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if node.lineno <= line <= end:
                matches.append((end - node.lineno, ".".join(nested)))
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(tree)
    return min(matches, default=(0, f"line-{line}"))[1]


def ruff_violations(root: Path) -> set[str]:
    """Collect stable keys that retain function and occurrence identity."""
    ruff = Path(sys.executable).with_name("ruff")
    result = subprocess.run(
        [
            str(ruff),
            "check",
            "--config",
            str(PROJECT_ROOT / "pyproject.toml"),
            "--output-format",
            "json",
            ".",
        ],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )
    if result.returncode not in (0, 1):
        print(f"ruff failed to run:\n{result.stderr}", file=sys.stderr)
        raise SystemExit(2)
    grouped: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for item in json.loads(result.stdout or "[]"):
        code = item.get("code") or ""
        if code not in BASELINED_RULES:
            continue
        path = Path(item["filename"]).resolve().relative_to(root)
        line = int(item["location"]["row"])
        grouped[code, path.as_posix(), function_at(root / path, line)].append(line)
    return {
        f"{code} {path}:{function}#{ordinal}"
        for (code, path, function), lines in grouped.items()
        for ordinal, _line in enumerate(sorted(lines), start=1)
    }


def size_violations(root: Path) -> set[str]:
    """Collect size keys from the requested snapshot, including hook code."""
    checker = root / "_githooks" / "check_code_size.py"
    result = subprocess.run(
        [sys.executable, str(checker), "--root", str(root)],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )
    if result.returncode != 0:
        print(f"size checker failed to run:\n{result.stderr}", file=sys.stderr)
        raise SystemExit(2)
    found: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "(ceiling" not in stripped:
            continue
        subject = stripped.split(" is ", 1)[0]
        if " is " not in stripped:
            subject = stripped.rsplit(": ", 1)[0]
        found.add(f"SIZE {subject}")
    return found


def main() -> int:
    """Compare a repository snapshot with the baseline or deliberately update it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    baseline_path = args.baseline.resolve()
    current = ruff_violations(root) | size_violations(root)
    if args.update:
        if root != PROJECT_ROOT or baseline_path != BASELINE_PATH:
            print("Refusing to update the canonical baseline from a snapshot.")
            return 2
        baseline_path.write_text(
            json.dumps(sorted(current), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Baseline updated: {len(current)} known violations recorded.")
        return 0
    if not baseline_path.exists():
        print(f"No baseline at {baseline_path}.")
        return 2
    baseline = set(json.loads(baseline_path.read_text(encoding="utf-8")))
    new = current - baseline
    fixed = baseline - current
    if fixed:
        print(
            f"{len(fixed)} baselined violation(s) fixed; update the baseline to lock them in."
        )
    if new:
        print(f"{len(new)} NEW violation(s):")
        for item in sorted(new):
            print(f"  + {item}")
        return 1
    print(f"No new violations. Baseline holds at {len(baseline)} known.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
