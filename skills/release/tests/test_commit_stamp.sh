#!/usr/bin/env bash
# Outcome-based tests for commit-stamp.sh — the git stage/commit/self-push
# behavior extracted from the stamp-changelog action (issue #284). Each test
# runs the real script against an isolated temporary bare remote + work clone
# and asserts on the emitted JSON outcome plus the remote's resulting state,
# covering:
#   - self-push: a stamped CHANGELOG lands on the branch as one [skip ci] commit
#     and the script prints {"outcome":"pushed"};
#   - no-op: an already-headed CHANGELOG prints {"outcome":"noop"}, no push;
#   - commit=false: the CHANGELOG is staged, {"outcome":"staged"}, never pushed;
#   - [skip ci] is appended when the caller's message lacks it;
#   - a non-branch (tag) ref is refused — no commit, no tag-named branch;
#   - an unrelated staged file is NOT swept into the stamp commit.
#
# Run: bash skills/release/tests/test_commit_stamp.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/commit-stamp.sh"
[[ -f "$SCRIPT" ]] || { echo "fatal: commit-stamp.sh not found at $SCRIPT" >&2; exit 2; }

TESTTMP="$(mktemp -d)" || { echo "fatal: mktemp -d failed" >&2; exit 2; }
# Named handler ending `return 0`, not a bare `trap 'rm -rf ...'`: the EXIT
# trap's final command status can become the process's exit status, so a failed
# cleanup would turn an all-green run non-zero and flake CI
# (rules/error-handling.md Shell Error Handling).
cleanup_tmp() {
  if [[ -n "${TESTTMP:-}" ]]; then
    if ! rm -rf "$TESTTMP"; then
      echo "warning: could not remove temp dir ${TESTTMP} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_tmp EXIT

FAIL_COUNT=0
PASS_COUNT=0

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# Create an isolated bare remote + work clone on branch `main` seeded with one
# headed CHANGELOG commit. Echoes the sandbox root; work is $root/work, remote
# is $root/remote.git.
_sandbox() {
  local root
  root="$(mktemp -d "$TESTTMP/sbx.XXXXXX")" || { echo "fatal: sandbox mktemp -d failed" >&2; return 1; }
  git init --bare -q -b main "$root/remote.git"
  # init + remote add rather than clone: cloning the empty bare repo warns on
  # stderr, and suppressing that warning would suppress real failures too.
  git init -q -b main "$root/work"
  git -C "$root/work" remote add origin "$root/remote.git"
  git -C "$root/work" config user.name "seed"
  git -C "$root/work" config user.email "seed@example.com"
  printf '# Changelog\n\n## 0.1.0 — 2026-01-01\n\n### seed\n' > "$root/work/CHANGELOG.md"
  git -C "$root/work" add CHANGELOG.md
  git -C "$root/work" commit -q -m "seed"
  git -C "$root/work" push -q -u origin main
  echo "$root"
}

_remote_sha()   { git -C "$1/remote.git" rev-parse main; }
_remote_msg()   { git -C "$1/remote.git" log -1 --format=%B main; }
_remote_count() { git -C "$1/remote.git" rev-list --count main; }
# Files changed by the remote's tip commit.
_remote_tip_files() { git -C "$1/remote.git" show --name-only --format= main; }

# Run the script in $work. The caller captures stdout (the JSON outcome) via
# command substitution; stderr (progress prose, and any failure diagnostic)
# flows through rather than being suppressed (rules/error-handling.md).
_run_json() {
  local root="$1"; shift
  ( cd "$root/work" && bash "$SCRIPT" "$@" )
}

# --- test bodies ---

t_self_push_lands_one_skip_ci_commit() {
  local root; root="$(_sandbox)"
  printf '\n### new entry\n' >> "$root/work/CHANGELOG.md"
  local out; out="$(_run_json "$root" CHANGELOG.md true "Stamp the version [skip ci]" main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | jq -e '.outcome == "pushed"' >/dev/null \
    || { echo "    FAIL: expected outcome=pushed, got: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "2" ]] || { echo "    FAIL: expected 2 remote commits" >&2; return 1; }
  [[ "$(_remote_msg "$root")" == *"[skip ci]"* ]] || { echo "    FAIL: remote commit missing [skip ci]" >&2; return 1; }
  git -C "$root/remote.git" show main:CHANGELOG.md | grep -q "new entry" \
    || { echo "    FAIL: remote CHANGELOG missing the stamped entry" >&2; return 1; }
}

t_noop_when_nothing_staged() {
  local root; root="$(_sandbox)"
  local before; before="$(_remote_sha "$root")"
  local out; out="$(_run_json "$root" CHANGELOG.md true "Stamp [skip ci]" main branch)" \
    || { echo "    FAIL: script exited non-zero on no-op" >&2; return 1; }
  echo "$out" | jq -e '.outcome == "noop"' >/dev/null \
    || { echo "    FAIL: expected outcome=noop, got: $out" >&2; return 1; }
  [[ "$before" == "$(_remote_sha "$root")" ]] || { echo "    FAIL: remote advanced on a no-op" >&2; return 1; }
}

t_commit_false_stages_without_pushing() {
  local root; root="$(_sandbox)"
  printf '\n### staged entry\n' >> "$root/work/CHANGELOG.md"
  local before; before="$(_remote_sha "$root")"
  local out; out="$(_run_json "$root" CHANGELOG.md false "Stamp [skip ci]" main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | jq -e '.outcome == "staged" and .changed == true' >/dev/null \
    || { echo "    FAIL: expected outcome=staged changed=true, got: $out" >&2; return 1; }
  git -C "$root/work" diff --cached --name-only | grep -qx "CHANGELOG.md" \
    || { echo "    FAIL: CHANGELOG not staged under commit=false" >&2; return 1; }
  [[ "$(git -C "$root/work" rev-list --count HEAD)" == "1" ]] \
    || { echo "    FAIL: a local commit was created under commit=false" >&2; return 1; }
  [[ "$before" == "$(_remote_sha "$root")" ]] || { echo "    FAIL: remote advanced under commit=false" >&2; return 1; }
}

t_skip_ci_appended_when_absent() {
  local root; root="$(_sandbox)"
  printf '\n### entry\n' >> "$root/work/CHANGELOG.md"
  _run_json "$root" CHANGELOG.md true "Stamp without a marker" main branch >/dev/null \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  [[ "$(_remote_msg "$root")" == *"[skip ci]"* ]] \
    || { echo "    FAIL: [skip ci] not appended to a message lacking it" >&2; return 1; }
}

t_refuses_non_branch_ref() {
  local root; root="$(_sandbox)"
  printf '\n### entry\n' >> "$root/work/CHANGELOG.md"
  local before; before="$(_remote_sha "$root")"
  local err rc
  err="$( ( cd "$root/work" && bash "$SCRIPT" CHANGELOG.md true "Stamp [skip ci]" v1.2.3 tag ) 2>&1 >/dev/null )"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit on a tag ref" >&2; return 1; }
  [[ "$err" == *"branch"* ]] || { echo "    FAIL: error message missing 'branch': $err" >&2; return 1; }
  [[ "$before" == "$(_remote_sha "$root")" ]] || { echo "    FAIL: remote main advanced on a tag ref" >&2; return 1; }
  if git -C "$root/remote.git" rev-parse --verify --quiet v1.2.3 >/dev/null; then
    echo "    FAIL: a tag-named branch was created on the remote" >&2; return 1
  fi
  [[ "$(git -C "$root/work" rev-list --count HEAD)" == "1" ]] \
    || { echo "    FAIL: a local commit was created before the ref-type refusal" >&2; return 1; }
}

t_does_not_sweep_unrelated_staged_file() {
  local root; root="$(_sandbox)"
  printf '\n### entry\n' >> "$root/work/CHANGELOG.md"
  # A caller left an unrelated file staged before the stamp step ran.
  printf 'unrelated\n' > "$root/work/unrelated.txt"
  git -C "$root/work" add unrelated.txt
  _run_json "$root" CHANGELOG.md true "Stamp [skip ci]" main branch >/dev/null \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  local files; files="$(_remote_tip_files "$root")"
  echo "$files" | grep -qx "CHANGELOG.md" || { echo "    FAIL: stamp commit missing CHANGELOG.md" >&2; return 1; }
  if echo "$files" | grep -qx "unrelated.txt"; then
    echo "    FAIL: unrelated staged file was swept into the pushed commit" >&2; return 1
  fi
}

# --- driver ---

main() {
  echo "== commit-stamp.sh tests =="
  run "self-push lands one [skip ci] commit on the branch"   t_self_push_lands_one_skip_ci_commit
  run "no commit or push when nothing is staged"             t_noop_when_nothing_staged
  run "commit=false stages the CHANGELOG without pushing"    t_commit_false_stages_without_pushing
  run "[skip ci] appended when the message lacks it"         t_skip_ci_appended_when_absent
  run "a non-branch (tag) ref is refused"                    t_refuses_non_branch_ref
  run "an unrelated staged file is not swept into the commit" t_does_not_sweep_unrelated_staged_file
  echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
  [[ "$FAIL_COUNT" -eq 0 ]]
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
