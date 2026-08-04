#!/usr/bin/env python3
"""Reject exception handlers that neither Loguru-log nor raise an exception."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = ("src", "tests", "scripts", "_githooks")
LOG_METHODS = frozenset({"critical", "error", "exception", "warning"})


class _SignalVisitor(ast.NodeVisitor):
    """Find observable actions without counting deferred nested definitions."""

    def __init__(self, logger_names: set[str]) -> None:
        self.found = False
        self.logger_names = logger_names

    def visit_Raise(self, node: ast.Raise) -> None:
        """Record a raise statement."""
        self.found = True

    def visit_Call(self, node: ast.Call) -> None:
        """Record an approved Loguru call and otherwise inspect its children."""
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in self.logger_names
            and function.attr in LOG_METHODS
        ):
            self.found = True
            return
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Ignore code deferred inside a nested function."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Ignore code deferred inside a nested async function."""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Ignore code deferred inside a nested class."""


def targets(root: Path, named: list[str]) -> list[Path]:
    """Return explicit Python files or every file under maintained roots.

    Args:
        root: Repository snapshot root.
        named: Logical repository paths; empty selects all maintained Python.

    Returns:
        Sorted Python files to inspect.
    """
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


def bound_aliases(node: ast.AST, logger_names: set[str]) -> set[str]:
    """Return assignment targets derived from a known logger's ``bind`` call.

    Args:
        node: Candidate assignment node.
        logger_names: Names already proven to reference Loguru loggers.

    Returns:
        Newly derived simple-name aliases.
    """
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return set()
    value = node.value
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
        return set()
    receiver = value.func.value
    if value.func.attr != "bind" or not isinstance(receiver, ast.Name):
        return set()
    if receiver.id not in logger_names:
        return set()
    assigned = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in assigned if isinstance(target, ast.Name)}


def loguru_logger_names(tree: ast.AST) -> set[str]:
    """Return names proven to reference Loguru logger objects.

    Args:
        tree: Parsed module.

    Returns:
        Directly imported logger names and aliases derived through ``bind``.
    """
    names = {
        item.asname or item.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "loguru"
        for item in node.names
        if item.name == "logger"
    }
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            aliases = bound_aliases(node, names) - names
            if aliases:
                names.update(aliases)
                changed = True
    return names


def handler_is_observable(handler: ast.ExceptHandler, logger_names: set[str]) -> bool:
    """Return whether a handler logs through Loguru or raises.

    Args:
        handler: Exception handler to inspect.
        logger_names: Names statically proven to reference Loguru loggers.

    Returns:
        True when the handler contains a raise or approved logger call.
    """
    visitor = _SignalVisitor(logger_names)
    for statement in handler.body:
        visitor.visit(statement)
    return visitor.found


def main() -> int:
    """Report every unobservable exception handler and return a failure code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    root = args.root.resolve()
    failures: list[str] = []
    for path in targets(root, args.paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        logger_names = loguru_logger_names(tree)
        failures.extend(
            f"{path.relative_to(root)}:{handler.lineno}: except must Loguru-log or raise"
            for handler in ast.walk(tree)
            if isinstance(handler, ast.ExceptHandler)
            and not handler_is_observable(handler, logger_names)
        )
    if failures:
        print("Unobservable exception handlers found:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("Exception handling observability OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
