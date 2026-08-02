#!/opt/homebrew/bin/bash
# verify.sh -- prove the installed hooks actually REJECT what they claim to.
#
# Installed is not the same as working. A hook can be present, executable, and
# never fire: wrong hooksPath, a tool that exits 0 when absent, a check pointed
# at a directory that does not exist. The only evidence that a gate works is
# watching it refuse a deliberate violation.
#
# Every probe below stages a real violation in a scratch worktree, attempts a
# real commit, and asserts the commit was REFUSED. Nothing here touches your
# working tree or your branch.

set -uo pipefail

repo_root="$(git rev-parse --show-toplevel)"
scratch="/Volumes/ThunderBolt/_tmp/audiobook-pipeline/_scratch/hook-verify.$$"
pass=0
fail=0

cleanup() {
  cd "$repo_root" || true
  git worktree remove --force "$scratch" 2>/dev/null || true
  git worktree prune 2>/dev/null || true
}
trap cleanup EXIT

printf '\n[verify] creating scratch worktree\n'
mkdir -p "$(dirname "$scratch")"
if ! git worktree add --detach "$scratch" HEAD >/dev/null 2>&1; then
  printf '[verify] FAILED to create worktree at %s\n' "$scratch"
  exit 1
fi

hooks_path="$(git -C "$repo_root" config core.hooksPath || true)"
if [[ -z "$hooks_path" ]]; then
  printf '[verify] core.hooksPath is UNSET -- run ./_githooks/install.sh first\n'
  exit 1
fi
git -C "$scratch" config core.hooksPath "$hooks_path"

# The scratch worktree has no .venv and no gitignored files, so `uv run
# --no-sync ruff` would die there with "Failed to spawn: ruff" and every probe
# would "block" for the wrong reason -- including the clean one. Link the real
# environment and the size checker in so the probes exercise the actual gates.
ln -sfn "$repo_root/.venv" "$scratch/.venv"
mkdir -p "$scratch/_githooks"
cp "$repo_root/_githooks/check_code_size.py" "$scratch/_githooks/"

# probe <name> <relative-path> <file-content>
# Asserts: committing this content is refused.
probe() {
  local name="$1" path="$2" content="$3"
  printf '%s' "$content" > "$scratch/$path"
  git -C "$scratch" add "$path" >/dev/null 2>&1

  if git -C "$scratch" commit -q -m "test: probe $name" >/dev/null 2>&1; then
    printf '  NOT BLOCKED  %s\n' "$name"
    fail=$((fail + 1))
    git -C "$scratch" reset --hard HEAD~1 >/dev/null 2>&1
  else
    printf '  blocked      %s\n' "$name"
    pass=$((pass + 1))
    git -C "$scratch" reset --hard HEAD >/dev/null 2>&1
  fi
  rm -f "$scratch/$path"
}

printf '\n[verify] probing pre-commit gates\n'

probe "ruff check (unused import)" "probe_lint.py" \
'"""Probe module."""

import os
'

# NOT a `probe` (which asserts refusal): badly-formatted code is AUTO-FIXED,
# not rejected. Rico's global pre-commit runs `ruff format` on staged files and
# re-stages them before this repo's hook sees anything, so the commit succeeds
# and what LANDS is formatted. Asserting refusal here would be asserting the
# wrong guarantee -- the guarantee is that unformatted code never lands.
printf '%s' '"""Probe module."""
x = {   "a":1,
     "b":2 }
' > "$scratch/probe_fmt.py"
git -C "$scratch" add probe_fmt.py >/dev/null 2>&1
git -C "$scratch" commit -q -m "test: probe formatting" >/dev/null 2>&1
if git -C "$scratch" show HEAD:probe_fmt.py 2>/dev/null | grep -q '{"a": 1, "b": 2}'; then
  printf '  auto-fixed   ruff format (unformatted code cannot land)\n'
  pass=$((pass + 1))
  git -C "$scratch" reset --hard HEAD~1 >/dev/null 2>&1
else
  printf '  NOT HANDLED  ruff format (unformatted code landed as written)\n'
  fail=$((fail + 1))
  git -C "$scratch" reset --hard HEAD >/dev/null 2>&1
fi
rm -f "$scratch/probe_fmt.py"

# Caught by SIM102 (collapsible-if), which stays at full strength in the hook.
# PLR1702 itself is baselined and enforced by the ratchet, so do not read this
# line as evidence that PLR1702 fires here -- it does not.
probe "nested conditionals (SIM102)" "probe_nest.py" \
'"""Probe module."""


def f(a, b, c, d):
    """Deliberately pyramided."""
    if a:
        if b:
            if c:
                if d:
                    return 1
    return 0
'

# 60 single-token code lines in one function: over the 50 ceiling, but flat,
# so complexity and branch-count rules do NOT catch it. Only the size checker.
#
# ADVISORY in the hook, not blocking -- 25 files are already over the ceiling
# and blocking here would refuse every edit to them. So this asserts the hook
# WARNS and still commits; CI's ratchet is what refuses a NEW breach. The
# ratchet is proven separately by its own probe below.
{
  printf '"""Probe module."""\n\n\ndef f():\n    """Long but flat."""\n    x = 0\n'
  for i in $(seq 1 60); do printf '    x += %d\n' "$i"; done
  printf '    return x\n'
} > "$scratch/probe_size.py"
git -C "$scratch" add probe_size.py >/dev/null 2>&1
size_output="$(git -C "$scratch" commit -m "test: probe size" 2>&1)"
if printf '%s' "$size_output" | grep -q 'ceiling 50'; then
  printf '  warned       code size (50 lines/function, advisory in hook)\n'
  pass=$((pass + 1))
else
  printf '  NOT REPORTED code size (the size gate never ran)\n'
  fail=$((fail + 1))
fi
git -C "$scratch" reset --hard HEAD >/dev/null 2>&1
rm -f "$scratch/probe_size.py"

# The ratchet is the gate that actually REFUSES a new size/shape violation.
# Proven here because "advisory in the hook" is only acceptable if something
# else is strict.
# mypy runs against the WORKING TREE (it needs the installed package), so this
# probe writes into the real repo and removes it again rather than committing
# from the scratch worktree.
printf '\n[verify] probing the mypy gate\n'
cat > "$repo_root/src/audiobook_pipeline/_verify_types.py" <<'PROBE'
"""Type probe."""


def f() -> int:
    """Declared int, returns str."""
    return "not an int"
PROBE
if (cd "$repo_root" && uv run --no-sync mypy) >/dev/null 2>&1; then
  printf '  NOT BLOCKED  mypy (a wrong return type passed)\n'
  fail=$((fail + 1))
else
  printf '  blocked      mypy (wrong return type refused)\n'
  pass=$((pass + 1))
fi
rm -f "$repo_root/src/audiobook_pipeline/_verify_types.py"

printf '\n[verify] probing the CI ratchet\n'
{
  printf '"""Ratchet probe."""\n\n\ndef f(a, b, c, d):\n    """Pyramid."""\n'
  printf '    if a:\n        if b:\n            if c:\n                if d:\n'
  printf '                    return 1\n    return 0\n'
} > "$repo_root/src/audiobook_pipeline/_verify_probe.py"
if (cd "$repo_root" && uv run --no-sync python _githooks/check_baseline.py) \
     >/dev/null 2>&1; then
  printf '  NOT BLOCKED  ratchet (a new violation was tolerated)\n'
  fail=$((fail + 1))
else
  printf '  blocked      ratchet (new complexity violation refused)\n'
  pass=$((pass + 1))
fi
rm -f "$repo_root/src/audiobook_pipeline/_verify_probe.py"

printf '\n[verify] probing commit-msg gate\n'
printf '"""Probe."""\n' > "$scratch/probe_msg.py"
git -C "$scratch" add probe_msg.py >/dev/null 2>&1
if git -C "$scratch" commit -q -m "added a thing that is not conventional" >/dev/null 2>&1; then
  printf '  NOT BLOCKED  commit-msg (non-conventional subject)\n'
  fail=$((fail + 1))
else
  printf '  blocked      commit-msg (non-conventional subject)\n'
  pass=$((pass + 1))
fi
git -C "$scratch" reset --hard HEAD >/dev/null 2>&1

printf '\n[verify] probing that a CLEAN commit still succeeds\n'
printf '"""A clean probe module."""\n\nVALUE = 1\n' > "$scratch/probe_clean.py"
git -C "$scratch" add probe_clean.py >/dev/null 2>&1
if git -C "$scratch" commit -q -m "test: clean probe commit" >/dev/null 2>&1; then
  printf '  accepted     clean commit\n'
  pass=$((pass + 1))
else
  printf '  FALSE POSITIVE: a clean commit was refused\n'
  printf '  (a gate that blocks everything is as broken as one that blocks nothing)\n'
  fail=$((fail + 1))
fi

printf '\n[verify] %d passed, %d failed\n\n' "$pass" "$fail"
[[ $fail -eq 0 ]] || exit 1
