#!/usr/bin/env python3
"""Enforce the SIZE leg of the standard: code lines per file and per function.

WHY THIS SCRIPT EXISTS AT ALL

ruff has no file-length rule. `too-many-lines` is a pylint rule ruff never
ported, and there is no `max-lines` equivalent anywhere in its catalogue
(verified against `ruff rule --all`, 2026-07-31). ruff's only size rule is
PLR0915 (too-many-statements), and that counts STATEMENTS, not lines: the
monitor `check` function measured 56 code lines but only 8 statements, because
most of its length is one multi-line httpx call, a `with` block, and comments.
Statement count is a poor proxy for size -- it tracks branching density, not
how much a reader has to hold in their head.

So the two size ceilings the standard actually states -- 500 code lines per
file, 50 code lines per function -- have to be measured directly. That is this
script. It counts CODE LINES: physical lines that carry a token, excluding
blank lines, comment-only lines, and docstrings. The exemplars are deliberately
comment-dense (30-50% is fine when the comments earn their place), so counting
raw lines would punish exactly the thing the standard encourages. Comments are
free; code is what gets counted.

WHY SIZE IS A SEPARATE LEG FROM COMPLEXITY

Cyclomatic complexity (C901) and branch count (PLR0912) count BRANCHING. A
400-line function that branches three times scores low on both and passes. Size
is the third leg they miss: a function can be long and flat and still be doing
five jobs. The cost of logical separation is almost nothing, and the payoff is
one place to fix, one place to find, and clean logging in and out.

Usage:
    uv run python _githooks/check_code_size.py            # report, exit 0
    uv run python _githooks/check_code_size.py --check    # exit 1 on any breach
    uv run python _githooks/check_code_size.py --check FILE ...
"""

from __future__ import annotations

import argparse
import ast
import sys
import tokenize
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Hard ceilings, from _DOCS/STANDARDS-python.md ## Size. Code lines only.
FILE_MAX = 500
FUNCTION_MAX = 50

#: Directories scanned by default. Tests count too -- a 900-line test file is
#: as hard to navigate as a 900-line module, and the fixtures hide in it.
DEFAULT_ROOTS = ("src", "tests", "scripts")


def _docstring_lines(tree: ast.AST) -> set[int]:
    """Return every physical line occupied by a docstring.

    Docstrings are documentation, not code, and the standard counts them with
    the comments -- free. Only the FIRST statement of a module, class, or
    function is a docstring; a bare string expression anywhere else is code.

    Args:
        tree: Parsed module.

    Returns:
        Line numbers (1-indexed) covered by any docstring.
    """
    lines: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        if not isinstance(first.value.value, str):
            continue
        for line in range(first.lineno, (first.end_lineno or first.lineno) + 1):
            lines.add(line)
    return lines


def code_line_set(path: Path) -> set[int]:
    """Return the set of physical line numbers that carry code in a file.

    A line counts when it holds at least one real token -- not a comment, not
    whitespace, not part of a docstring. Tokenising rather than string-matching
    is what makes `#` inside a string, and a `)` alone on a continuation line,
    classify correctly.

    Args:
        path: Python file to measure.

    Returns:
        The set of code-bearing line numbers.
    """
    source = path.read_text(encoding="utf-8")
    doc_lines = _docstring_lines(ast.parse(source))
    skip = {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.COMMENT,
    }
    lines: set[int] = set()
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in skip:
                continue
            row = token.start[0]
            if row not in doc_lines:
                lines.add(row)
    return lines


def oversized_functions(path: Path, code_lines: set[int]) -> list[tuple[str, int]]:
    """Return functions whose code-line span exceeds FUNCTION_MAX.

    A function's size is the count of ITS code lines -- lines inside its body,
    intersected with the file's code-line set so its own docstring and comments
    do not inflate it. Nested functions are measured on their own; the enclosing
    span still includes them, which is correct: a function that needs a big
    helper inside it is still a big function.

    Args:
        path: File the functions live in.
        code_lines: The file's code-bearing line numbers.

    Returns:
        Pairs of (function name, code-line count) over the ceiling.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    over: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        span = range(node.body[0].lineno, (node.end_lineno or node.lineno) + 1)
        size = len(code_lines.intersection(span))
        if size > FUNCTION_MAX:
            over.append((node.name, size))
    return over


def check_file(path: Path) -> list[str]:
    """Measure one file and return a breach message for each ceiling exceeded.

    Args:
        path: Python file to check.

    Returns:
        Human-readable breach lines, empty when the file is within all ceilings.
    """
    code_lines = code_line_set(path)
    # Display path relative to the repo when possible. The pre-commit hook feeds
    # this script staged copies checked out under `.git/`, which are OUTSIDE
    # PROJECT_ROOT, so a bare `relative_to` would crash on exactly the path that
    # matters most. Fall back to the path as given.
    try:
        rel: Path | str = path.relative_to(PROJECT_ROOT)
    except ValueError:
        rel = path
    breaches: list[str] = []
    if len(code_lines) > FILE_MAX:
        breaches.append(f"{rel}: {len(code_lines)} code lines (ceiling {FILE_MAX})")
    for name, size in oversized_functions(path, code_lines):
        breaches.append(
            f"{rel}: {name}() is {size} code lines (ceiling {FUNCTION_MAX})"
        )
    return breaches


def iter_targets(explicit: list[str]) -> list[Path]:
    """Resolve the files to check: named paths, or the default roots.

    Args:
        explicit: Paths passed on the command line; empty means scan the roots.

    Returns:
        Existing Python files, sorted and de-duplicated.
    """
    if explicit:
        named = [Path(item).resolve() for item in explicit]
        return sorted({p for p in named if p.suffix == ".py" and p.is_file()})
    found: set[Path] = set()
    for root in DEFAULT_ROOTS:
        found.update((PROJECT_ROOT / root).rglob("*.py"))
    return sorted(found)


def main() -> int:
    """Run the size check and report.

    Returns:
        1 under --check when any file or function is over ceiling, else 0.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when any file or function exceeds its code-line ceiling",
    )
    parser.add_argument("paths", nargs="*", help="specific files; default scans roots")
    args = parser.parse_args()

    breaches: list[str] = []
    for target in iter_targets(args.paths):
        breaches.extend(check_file(target))

    if not breaches:
        print(
            f"Size OK: every file <= {FILE_MAX} and every function <= {FUNCTION_MAX} code lines."
        )
        return 0

    print(f"{len(breaches)} size breach(es):\n")
    for line in breaches:
        print(f"  {line}")

    if args.check:
        print("\nFAILED (--check). Split the file or extract from the function.")
        print("Logical separation is cheap; one place to fix beats five copies.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
