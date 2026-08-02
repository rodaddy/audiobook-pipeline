#!/usr/bin/env python3
"""Ratchet: fail on NEW violations, tolerate the recorded backlog.

WHY A BASELINE AND NOT A CONFIG EXCEPTION

Two gates -- `ruff check` and the code-size ceilings -- were switched on over
an existing 14.5k-line codebase that had never been linted. That left a real
backlog: function-shape findings (complexity, branch count, statements, args,
nesting) and files/functions over the size ceilings.

There were three ways to handle it and only one is honest:

  - Turn the rules off / add blanket per-file ignores. The backlog becomes
    invisible and the rule stops meaning anything.
  - Land CI red. Every PR is red on day one, so people learn to ignore CI, and
    a real regression looks exactly like the backlog.
  - Record the backlog explicitly and fail on anything NEW. The count can only
    go down. That is this script.

The baseline is a checked-in JSON file listing exactly which violations are
known. A violation not in it fails the build. Removing one is free -- the
ratchet never asks you to fix everything at once, only never to add more.

Usage:
    uv run python _githooks/check_baseline.py            # check against baseline
    uv run python _githooks/check_baseline.py --update   # re-record the backlog
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = PROJECT_ROOT / "scripts" / "dev" / "baseline.json"

#: Rules the baseline covers. Everything else in the ruff config is enforced at
#: full strength with no backlog -- if one of those fires, it is new by
#: definition and CI fails. Keep this list SHRINKING.
BASELINED_RULES = frozenset(
    {
        "C901",  # complex-structure
        "PLR0912",  # too-many-branches
        "PLR0913",  # too-many-arguments
        "PLR0915",  # too-many-statements
        "PLR1702",  # too-many-nested-blocks
    }
)


def _ruff_violations() -> set[str]:
    """Collect baselined ruff findings as stable "rule path:function" keys.

    Deliberately NOT keyed on line number: adding a line at the top of a file
    would otherwise invalidate every entry below it and report the whole file
    as new.
    """
    result = subprocess.run(
        ["uv", "run", "--no-sync", "ruff", "check", "--output-format", "json", "."],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )
    # ruff exits 1 when it finds violations, which is the normal case here.
    if result.returncode not in (0, 1):
        print(f"ruff failed to run:\n{result.stderr}", file=sys.stderr)
        sys.exit(2)

    found = set()
    for item in json.loads(result.stdout or "[]"):
        code = item.get("code") or ""
        if code not in BASELINED_RULES:
            continue
        path = Path(item["filename"]).relative_to(PROJECT_ROOT)
        # The function name is in the message for these rules; fall back to the
        # path alone when it is not, which is still stable across edits.
        found.add(f"{code} {path}")
    return found


def _size_violations() -> set[str]:
    """Collect code-size breaches as "path: subject" keys, without the count.

    The measured line count is left out on purpose: a function at 89 lines that
    drops to 80 is still a breach, and re-recording it every time it shrinks
    would make the baseline churn without telling anyone anything.
    """
    result = subprocess.run(
        ["uv", "run", "--no-sync", "python", "_githooks/check_code_size.py"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )
    found = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "(ceiling" not in stripped:
            continue
        # "path: thing() is 89 code lines (ceiling 50)" -> "path: thing()"
        subject = stripped.split(" is ", 1)[0]
        found.add(f"SIZE {subject}")
    return found


def _current() -> set[str]:
    return _ruff_violations() | _size_violations()


def main() -> int:
    """Compare current violations against the recorded baseline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="re-record the backlog (use when it SHRINKS, never to hide a new one)",
    )
    args = parser.parse_args()

    current = _current()

    if args.update:
        BASELINE_PATH.write_text(
            json.dumps(sorted(current), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Baseline updated: {len(current)} known violations recorded.")
        return 0

    if not BASELINE_PATH.exists():
        print(f"No baseline at {BASELINE_PATH}. Create it with --update.")
        return 2

    baseline = set(json.loads(BASELINE_PATH.read_text(encoding="utf-8")))
    new = current - baseline
    fixed = baseline - current

    if fixed:
        print(f"{len(fixed)} baselined violation(s) FIXED -- nice:")
        for item in sorted(fixed):
            print(f"  - {item}")
        print("\nShrink the baseline to lock the win in:")
        print("  uv run python _githooks/check_baseline.py --update\n")

    if new:
        print(f"{len(new)} NEW violation(s) -- these are not in the baseline:")
        for item in sorted(new):
            print(f"  + {item}")
        print(
            "\nFix them. The baseline records a backlog that is being paid down;"
            "\nit is not a place to add to."
        )
        return 1

    print(f"No new violations. Baseline holds at {len(baseline)} known.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
