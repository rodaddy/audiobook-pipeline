#!/usr/bin/env python3
"""Generate requirements.txt from pyproject.toml dependencies.

Reads the [project.dependencies] section from pyproject.toml and writes
a requirements.txt file for environments that don't use uv/pip-tools.

Usage (pre-commit hook or manual):
    python scripts/gen-requirements.py
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"

HEADER = """\
# Auto-generated from pyproject.toml by scripts/gen-requirements.py
# Do not edit manually -- update pyproject.toml instead.
"""


def parse_dependencies(pyproject_text: str) -> list[str]:
    """Extract dependencies from pyproject.toml (simple TOML parsing)."""
    deps = []
    in_deps = False
    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if stripped == "dependencies = [":
            in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                break
            # Extract quoted dependency string
            match = re.match(r'^\s*"([^"]+)"', stripped)
            if match:
                deps.append(match.group(1))
    return deps


def main() -> int:
    if not PYPROJECT.exists():
        print(f"Error: {PYPROJECT} not found", file=sys.stderr)
        return 1

    deps = parse_dependencies(PYPROJECT.read_text())
    if not deps:
        print("Warning: no dependencies found in pyproject.toml", file=sys.stderr)
        return 0

    content = HEADER + "\n".join(deps) + "\n"

    if REQUIREMENTS.exists() and REQUIREMENTS.read_text() == content:
        return 0

    REQUIREMENTS.write_text(content)
    print(f"  Updated {REQUIREMENTS.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
