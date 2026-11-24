#!/usr/bin/env bash
# Outcome-based tests for commit-stamp.sh — the git commit/self-push
# behavior extracted from the stamp-changelog action (issue #284). Each test
# runs the real script against an isolated temporary bare remote + work clone
# and asserts on the emitted JSON outcome plus the remote's resulting state,
# covering:
#   - self-push: a stamped CHANGELOG lands as one commit with the fixed
#     message and the script prints {"outcome":"pushed","ref":"main"};
#   - no-op: an already-headed CHANGELOG prints {"outcome":"noop"}, no push;
#   - a rejecting pre-commit hook keeps both branch tips at the seed commit;
#   - a non-branch (tag) ref is refused — no commit, no tag-named branch;
#   - the stamp commit changes only CHANGELOG and preserves unrelated staging.
#
# Run: bash tests/release/tests/test_commit_stamp.sh
# Stdout: one JSON line {"suite","passed","failed"}; progress goes to stderr.
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../skills/release" && pwd)/commit-stamp.sh"
[[ -f "$SCRIPT" ]] || { echo "fatal: commit-stamp.sh not found at $SCRIPT" >&2; exit 2; }

TESTTMP="$(mktemp -d "${TMPDIR:-/tmp}/commit-stamp-test.XXXXXX")" || { echo "fatal: mktemp -d failed" >&2; exit 2; }
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
    echo "  pass: $name" >&2
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
_remote_count() { git -C "$1/remote.git" rev-list --count main; }

# Run the script in $work. The caller captures stdout (the JSON outcome) via
# command substitution; stderr (progress prose, and any failure diagnostic)
# flows through rather than being suppressed (rules/error-handling.md).
_run_json() {
  local root="$1"; shift
  ( cd "$root/work" && bash "$SCRIPT" "$@" )
}

# --- test bodies ---

t_self_push_lands_one_commit() {
  local root; root="$(_sandbox)"
  printf '\n### new entry\n' >> "$root/work/CHANGELOG.md"
  local out; out="$(_run_json "$root" CHANGELOG.md main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | jq -e '. == {"outcome":"pushed","ref":"main"}' >/dev/null \
    || { echo "    FAIL: expected pushed outcome for main, got: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "2" ]] || { echo "    FAIL: expected 2 remote commits" >&2; return 1; }
  git -C "$root/remote.git" cat-file commit main |
    python3 -c 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().split(b"\n\n", 1)[1])' |
    cmp -s - <(printf 'Stamp CHANGELOG for the published version\n') \
    || { echo "    FAIL: raw remote commit message differs from the fixed message" >&2; return 1; }
  git -C "$root/remote.git" show main:CHANGELOG.md | grep -q "new entry" \
    || { echo "    FAIL: remote CHANGELOG missing the stamped entry" >&2; return 1; }
}

t_noop_when_changelog_unchanged() {
  local root; root="$(_sandbox)"
  local before; before="$(_remote_sha "$root")"
  local out; out="$(_run_json "$root" CHANGELOG.md main branch)" \
    || { echo "    FAIL: script exited non-zero on no-op" >&2; return 1; }
  echo "$out" | jq -e '. == {"outcome":"noop"}' >/dev/null \
    || { echo "    FAIL: expected outcome=noop, got: $out" >&2; return 1; }
  [[ "$before" == "$(_remote_sha "$root")" ]] || { echo "    FAIL: remote advanced on a no-op" >&2; return 1; }
  [[ "$before" == "$(git -C "$root/work" rev-parse refs/heads/main)" ]] \
    || { echo "    FAIL: local branch advanced on a no-op" >&2; return 1; }
}

t_rejecting_hook_leaves_refs_unchanged() {
  local root before local_after remote_after rc=0 out hook
  root="$(_sandbox)" || return 1
  before="$(_remote_sha "$root")" || return 1
  printf '\n### entry\n' >> "$root/work/CHANGELOG.md" || return 1
  hook="$root/work/.git/hooks/pre-commit"
  git -C "$root/work" config core.hooksPath "${hook%/*}" || return 1
  cat > "$hook" <<'HOOK' || return 1
#!/usr/bin/env bash
set -euo pipefail
echo 'fixture pre-commit hook rejected the stamp' >&2
exit 1
HOOK
  chmod +x "$hook" || return 1
  out=$(_run_json "$root" CHANGELOG.md main branch 2>"$root/err") || rc=$?
  local_after="$(git -C "$root/work" rev-parse refs/heads/main)" || return 1
  remote_after="$(_remote_sha "$root")" || return 1
  [[ "$local_after" == "$before" && "$remote_after" == "$before" ]] \
    || { echo "    FAIL: expected both refs at $before, got local $local_after and remote $remote_after" >&2; return 1; }
  [[ "$rc" -ne 0 && -z "$out" && "$(cat "$root/err")" == *"fixture pre-commit hook rejected the stamp"* ]] \
    || { echo "    FAIL: expected pre-commit refusal and empty stdout (exit $rc), got: $out" >&2; return 1; }
}

t_refuses_non_branch_ref() {
  local root; root="$(_sandbox)"
  printf '\n### entry\n' >> "$root/work/CHANGELOG.md"
  local before; before="$(_remote_sha "$root")"
  local err rc
  err="$(_run_json "$root" CHANGELOG.md v1.2.3 tag 2>&1 >/dev/null)"; rc=$?
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
  _run_json "$root" CHANGELOG.md main branch >/dev/null \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  local files staged
  files="$(git -C "$root/remote.git" show --name-only --format= main)" || return 1
  [[ "$files" == "CHANGELOG.md" ]] \
    || { echo "    FAIL: expected only CHANGELOG.md in the pushed diff, got: $files" >&2; return 1; }
  staged="$(git -C "$root/work" diff --cached --name-only)" || return 1
  [[ "$staged" == "unrelated.txt" ]] \
    || { echo "    FAIL: expected unrelated.txt to remain staged, got: $staged" >&2; return 1; }
}

# --- driver ---

run_suite() {
  echo "test_commit_stamp.sh" >&2
  run "self-push lands one commit with the exact fixed message" t_self_push_lands_one_commit
  run "unchanged CHANGELOG leaves both branch tips in place" t_noop_when_changelog_unchanged
  run "a rejecting pre-commit hook leaves both branch tips in place" t_rejecting_hook_leaves_refs_unchanged
  run "a non-branch (tag) ref is refused" t_refuses_non_branch_ref
  run "self-push changes only CHANGELOG and preserves unrelated staging" t_does_not_sweep_unrelated_staged_file
  printf '{"suite":"test_commit_stamp.sh","passed":%d,"failed":%d}\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ $FAIL_COUNT -eq 0 ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_suite
fi
