#!/usr/bin/env python3
"""Generate README.md files from __init__.py docstrings.

Walks the package tree, extracts module docstrings from __init__.py files,
and writes a README.md in each directory that has one.

Usage (pre-commit hook or manual):
    python scripts/gen-readme.py
"""

import ast
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "audiobook_pipeline"


def extract_docstring(init_file: Path) -> str | None:
    """Extract the module docstring from an __init__.py file."""
    try:
        tree = ast.parse(init_file.read_text())
    except SyntaxError:
        return None
    return ast.get_docstring(tree)


def generate_readme(package_dir: Path, docstring: str) -> str:
    """Generate README.md content from a module docstring."""
    module_name = package_dir.name
    lines = [f"# {module_name}", ""]
    lines.extend(docstring.splitlines())
    lines.append("")
    lines.append("---")
    lines.append("*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    changed = 0
    for init_file in sorted(PACKAGE_ROOT.rglob("__init__.py")):
        docstring = extract_docstring(init_file)
        if not docstring:
            continue

        readme = init_file.parent / "README.md"
        content = generate_readme(init_file.parent, docstring)

        if readme.exists() and readme.read_text() == content:
            continue

        readme.write_text(content)
        print(f"  Updated {readme.relative_to(PACKAGE_ROOT.parent.parent)}")
        changed += 1

    if changed:
        print(f"\n{changed} README(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
