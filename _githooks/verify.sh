#!/opt/homebrew/bin/bash
# verify.sh -- prove that the current commit gates reject representative defects.
#
# All probes run in a detached worktree below the configured ThunderBolt temp
# root. The live checkout supplies only the committed snapshot and its virtual
# environment; no probe file, index update, or hook configuration is written
# there.

set -uo pipefail

repo_root="$(git rev-parse --show-toplevel)"

# Scratch space for the throwaway worktrees this check builds. The maintainer's
# machine keeps them off the system disk, but that path exists nowhere else, so
# fall back to the OS temp directory rather than failing on every other clone.
if [[ -d /Volumes/ThunderBolt/_tmp && -w /Volumes/ThunderBolt/_tmp ]]; then
  temp_root="/Volumes/ThunderBolt/_tmp/audiobook-pipeline"
else
  temp_root="${TMPDIR:-/tmp}/audiobook-pipeline"
fi
run_id="hook-verify-$(date +%Y%m%d-%H%M%S)-$$"
worktree_root="$temp_root/_worktrees"
archive_root="$temp_root/_archive"
artifact_root="$temp_root/_scratch/$run_id"
archive_path="$archive_root/$run_id"
scratch="$worktree_root/$run_id"
hooks_dir="$artifact_root/hooks"
global_hooks="$(git config --global --get core.hooksPath || true)"
pass=0
fail=0
worktree_created=0
finalized=0

report() {
  printf '%s\n' "$*"
  printf '%s\n' "$*" >> "$artifact_root/proof.log"
}

git_scratch() {
  git -c core.hooksPath="$hooks_dir" -C "$scratch" "$@"
}

archive_failed_probe() {
  local name="$1"
  local path="$2"

  git_scratch restore --staged -- "$path"
  if [[ -e "$scratch/$path" || -L "$scratch/$path" ]]; then
    mkdir -p "$artifact_root/failed-probes"
    mv "$scratch/$path" "$artifact_root/failed-probes/$name.py"
  fi
}

reject_probe() {
  local name="$1"
  local path="$2"
  local marker="$3"
  local output

  git_scratch add "$path"
  if output="$(git_scratch commit -m "test: hook proof $name" 2>&1)"; then
    report "  NOT BLOCKED  $name"
    report "$output"
    fail=$((fail + 1))
    return
  fi

  if [[ "$output" == *"$marker"* ]]; then
    report "  blocked      $name"
    pass=$((pass + 1))
  else
    report "  WRONG BLOCK  $name (expected: $marker)"
    report "$output"
    fail=$((fail + 1))
  fi
  archive_failed_probe "$name" "$path"
}

write_hook_wrappers() {
  mkdir -p "$hooks_dir"
  if [[ -n "$global_hooks" && -x "$global_hooks/pre-commit" ]]; then
    {
      printf '%s\n' '#!/opt/homebrew/bin/bash' 'set -uo pipefail'
      printf 'global_hook=%q\n' "$global_hooks/pre-commit"
      printf 'repo_hook=%q\n' "$scratch/_githooks/pre-commit"
      # shellcheck disable=SC2016
      printf '%s\n' '"$global_hook" "$@" || exit $?' '"$repo_hook" "$@"'
    } > "$hooks_dir/pre-commit"
  else
    {
      printf '%s\n' '#!/opt/homebrew/bin/bash' 'set -uo pipefail'
      printf 'repo_hook=%q\n' "$scratch/_githooks/pre-commit"
      # shellcheck disable=SC2016
      printf '%s\n' '"$repo_hook" "$@"'
    } > "$hooks_dir/pre-commit"
  fi
  {
    printf '%s\n' '#!/opt/homebrew/bin/bash' 'set -uo pipefail'
    printf 'repo_hook=%q\n' "$scratch/_githooks/commit-msg"
    # shellcheck disable=SC2016
    printf '%s\n' '"$repo_hook" "$@"'
  } > "$hooks_dir/commit-msg"
  chmod +x "$hooks_dir/pre-commit" "$hooks_dir/commit-msg"
}

format_probe() {
  local path="probe_format.py"
  local original='"""Formatting proof module."""
VALUE = {   "a":1,
     "b":2 }'
  local output
  local landed

  printf '%s' "$original" > "$scratch/$path"
  git_scratch add "$path"
  if output="$(git_scratch commit -m "test: hook proof format" 2>&1)"; then
    landed="$(git -C "$scratch" show "HEAD:$path")"
    if [[ "$landed" != "$original" ]] && "$scratch/.venv/bin/ruff" format --check "$scratch/$path"; then
      report "  reformatted  formatting cannot land unformatted"
      pass=$((pass + 1))
    else
      report "  NOT HANDLED  formatting landed unchanged or invalid"
      report "$output"
      fail=$((fail + 1))
    fi
    return
  fi

  if [[ "$output" == *"ruff format"* ]]; then
    report "  blocked      formatting cannot land unformatted"
    pass=$((pass + 1))
  else
    report "  WRONG BLOCK  formatting"
    report "$output"
    fail=$((fail + 1))
  fi
  archive_failed_probe "format" "$path"
}

clean_commit_probe() {
  local path="src/audiobook_pipeline/probe_clean.py"
  local output

  printf '%s\n' '"""A clean hook proof module."""' '' 'VALUE: int = 1' > "$scratch/$path"
  git_scratch add "$path"
  if output="$(git_scratch commit -m "test: clean hook proof" 2>&1)"; then
    report "  accepted     clean commit"
    pass=$((pass + 1))
  else
    report "  FALSE POSITIVE: clean commit was refused"
    report "$output"
    fail=$((fail + 1))
    archive_failed_probe "clean" "$path"
  fi
}

finalize() {
  local result="$1"
  local scratch_status

  [[ $finalized -eq 0 ]] || return "$result"
  finalized=1
  if [[ $worktree_created -eq 1 ]]; then
    if [[ -d "$scratch/.venv" ]] \
      && [[ -L "$scratch/.venv/bin/python" ]] \
      && [[ -L "$scratch/.venv/bin/ruff" ]] \
      && [[ -L "$scratch/.venv/bin/mypy" ]]; then
      mv "$scratch/.venv" "$artifact_root/tool-links"
    elif [[ -e "$scratch/.venv" || -L "$scratch/.venv" ]]; then
      report "[verify] BLOCKED: scratch virtual environment is not the proof tool-link set"
      result=1
    fi
    scratch_status="$(git -C "$scratch" status --porcelain)"
    if [[ -z "$scratch_status" ]]; then
      report "[verify] scratch status clean"
      if git -C "$repo_root" worktree remove "$scratch"; then
        report "[verify] scratch worktree removed"
        git -C "$repo_root" worktree prune
      else
        report "[verify] BLOCKED: ordinary worktree removal failed"
        result=1
      fi
    else
      report "[verify] BLOCKED: scratch worktree is not clean"
      report "$scratch_status"
      result=1
    fi
  fi
  if [[ -d "$artifact_root" ]]; then
    mv "$artifact_root" "$archive_path"
    printf '[verify] artifacts archived at %s\n' "$archive_path"
  fi
  return "$result"
}

on_exit() {
  local result=$?

  trap - EXIT
  finalize "$result"
  exit $?
}

trap on_exit EXIT

mkdir -p "$worktree_root" "$archive_root" "$artifact_root"
report "[verify] creating detached scratch worktree"
if ! git -C "$repo_root" worktree add --detach "$scratch" HEAD; then
  report "[verify] FAILED to create scratch worktree at $scratch"
  exit 1
fi
worktree_created=1

if [[ ! -d "$repo_root/.venv" ]]; then
  report "[verify] BLOCKED: live repository virtual environment is missing"
  exit 1
fi
if [[ -e "$scratch/.venv" || -L "$scratch/.venv" ]]; then
  report "[verify] BLOCKED: scratch worktree already contains .venv"
  exit 1
fi
mkdir -p "$scratch/.venv/bin"
for tool in python ruff mypy; do
  if [[ ! -x "$repo_root/.venv/bin/$tool" ]]; then
    report "[verify] BLOCKED: live repository tool is missing: $tool"
    exit 1
  fi
  ln -s "$repo_root/.venv/bin/$tool" "$scratch/.venv/bin/$tool"
done
write_hook_wrappers

report "[verify] probing rejection gates"
printf '%s\n' '"""Unused import proof module."""' '' 'import os' > "$scratch/src/audiobook_pipeline/probe_unused.py"
reject_probe "unused import" "src/audiobook_pipeline/probe_unused.py" "unused-import"

format_probe

printf '%s\n' \
  '"""Nested conditional proof module."""' \
  '' \
  'def nested(first: bool, second: bool, third: bool, fourth: bool) -> int:' \
  '    """Return one only through a deliberately nested path."""' \
  '    if first:' \
  '        if second:' \
  '            if third:' \
  '                if fourth:' \
  '                    return 1' \
  '    return 0' > "$scratch/src/audiobook_pipeline/probe_nested.py"
reject_probe "nested conditional" "src/audiobook_pipeline/probe_nested.py" "collapsible-if"

{
  printf '%s\n' '"""Function-size proof module."""' ''
  printf '%s\n' 'def overlong() -> int:' '    """Exceed the fifty-code-line ceiling."""' '    value = 0'
  for number in $(seq 1 51); do
    printf '    value += %s\n' "$number"
  done
  printf '%s\n' '    return value'
} > "$scratch/src/audiobook_pipeline/probe_size.py"
reject_probe "function over fifty code lines" "src/audiobook_pipeline/probe_size.py" "SIZE src/audiobook_pipeline/probe_size.py: overlong()"

printf '%s\n' \
  '"""Mypy proof module."""' \
  '' \
  'def wrong_return() -> int:' \
  '    """Return an intentionally incompatible value."""' \
  '    return "not an int"' > "$scratch/src/audiobook_pipeline/probe_types.py"
reject_probe "mypy bad return" "src/audiobook_pipeline/probe_types.py" "return-value"

printf '%s\n' '"""Commit subject proof module."""' '' 'VALUE = 1' > "$scratch/probe_message.py"
git_scratch add probe_message.py
if commit_message_output="$(git_scratch commit -m "not a conventional commit message" 2>&1)"; then
  report "  NOT BLOCKED  commit message"
  report "$commit_message_output"
  fail=$((fail + 1))
elif [[ "$commit_message_output" == *"[commit-msg] BLOCKED"* ]]; then
  report "  blocked      commit message"
  pass=$((pass + 1))
else
  report "  WRONG BLOCK  commit message"
  report "$commit_message_output"
  fail=$((fail + 1))
fi
archive_failed_probe "commit-message" "probe_message.py"

clean_commit_probe

report "[verify] $pass passed, $fail failed"
if [[ $fail -ne 0 ]]; then
  exit 1
fi
