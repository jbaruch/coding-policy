#!/usr/bin/env bash
# Run all install-reviewer preconditions and report them as one JSON
# result. The skill invokes this before any mutation so every preflight
# failure is surfaced together, not one-at-a-time. Checks cover: git
# worktree, GitHub CLI installation + auth, gh-aw extension, tile
# template presence, origin remote, and (mode-dependent) branch state.
#
# Usage: preflight.sh [--override]
#   --override    Upgrade existing scaffolded reviewers in place (instead
#                 of failing on their existence per the install-mode
#                 safety gate). Skips the branch-not-local /
#                 branch-not-remote checks (the upgrade branch can
#                 legitimately already exist from a prior in-flight
#                 attempt) and adds a no-uncommitted-target-edits check
#                 so the consumer commits or stashes their dirty
#                 working tree before the upgrade overwrites it.
# Out:   one JSON object on stdout:
#          {"ok": bool,
#           "override": bool,
#           "failures": [{"check": "<name>", "reason": "<human text>"}, ...],
#           "warnings": [{"check": "<name>", "reason": "<human text>"}, ...]}
#        When ok is false, each failure includes a concrete recovery
#        command where applicable. Warnings are informational only —
#        they surface advisory findings and never set ok to false or
#        change the exit code.
# Exit:  0 if ok is true; 1 if any check fails

set -euo pipefail

OVERRIDE_MODE=0
for arg in "$@"; do
  case "$arg" in
    --override) OVERRIDE_MODE=1 ;;
    *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
  esac
done

# jq is required for emitting the structured JSON contract documented
# above. Without this early gate the script would die at the final jq
# invocation with `jq: command not found` and the agent parsing our
# stdout would have nothing to work with. Hand-roll the missing-jq
# diagnostic so the failure still satisfies the contract — every
# other failure mode below depends on jq being present.
if ! command -v jq >/dev/null 2>&1; then
  override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"
  cat <<EOF
{"ok": false, "override": ${override_json}, "failures": [{"check": "jq-installed", "reason": "jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run"}], "warnings": []}
EOF
  exit 1
fi

# If we're inside a git worktree, run from its root so the TEMPLATE path
# below resolves the same way regardless of the caller's cwd. If we're
# NOT in a worktree, the check_in_git_worktree step below will fail
# cleanly; don't exit here — we want to surface all preflight failures
# as structured JSON, not die early.
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -n "$repo_root" ]]; then
  cd "$repo_root"
fi

if (( OVERRIDE_MODE == 1 )); then
  BRANCH="feat/upgrade-coding-policy-review"
else
  BRANCH="feat/add-coding-policy-review"
fi
TEMPLATE_DIR=".tessl/tiles/jbaruch/coding-policy/skills/install-reviewer"
TEMPLATES=(
  "${TEMPLATE_DIR}/review-openai.md"
  "${TEMPLATE_DIR}/review-anthropic.md"
)
TARGETS=(
  ".github/workflows/review-openai.md"
  ".github/workflows/review-openai.lock.yml"
  ".github/workflows/review-anthropic.md"
  ".github/workflows/review-anthropic.lock.yml"
  ".github/aw/actions-lock.json"
  ".gitattributes"
)

declare -a failures=()
declare -a warnings=()

push_failure() {
  failures+=("{\"check\":\"$1\",\"reason\":\"$2\"}")
}

push_warning() {
  warnings+=("{\"check\":\"$1\",\"reason\":\"$2\"}")
}

check_in_git_worktree() {
  git rev-parse --git-dir >/dev/null 2>&1 || \
    push_failure "in-git-worktree" "Not inside a git worktree — run the skill from the root of the consumer repo's git checkout"
}

check_origin_remote() {
  git remote get-url origin >/dev/null 2>&1 || \
    push_failure "origin-remote" "No git remote named 'origin' — add one with 'git remote add origin <url>' before re-running (the push step assumes origin exists)"
}

check_gh_installed() {
  command -v gh >/dev/null 2>&1 || \
    push_failure "gh-installed" "GitHub CLI not found on PATH — install from https://cli.github.com/"
}

check_gh_authenticated() {
  gh auth status >/dev/null 2>&1 || \
    push_failure "gh-authenticated" "GitHub CLI not authenticated — run 'gh auth login'"
}

check_gh_aw_installed() {
  gh aw --version >/dev/null 2>&1 || \
    push_failure "gh-aw-installed" "gh-aw extension missing — run 'gh extension install github/gh-aw'"
}

# v0.71.0 replaced the deprecated `bypassPermissions` Claude SDK flag with
# `acceptEdits`. Older gh-aw compiles lock files that current Claude SDK
# versions reject, so refuse to scaffold against < v0.71.0. github/gh-aw
# marks releases >= v0.69.0 as prerelease, so `gh extension install
# github/gh-aw` installs the latest stable (v0.68.3) by default — the
# recovery command pins explicitly to a known-good prerelease.
check_gh_aw_min_version() {
  local raw min major minor patch min_major min_minor min_patch
  # `gh aw --version` writes to stderr (typical gh-extension idiom), so merge
  # streams before parsing rather than discarding stderr.
  raw=$(gh aw --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1) || true
  if [[ -z "$raw" ]]; then
    push_failure "gh-aw-min-version" "Could not parse 'gh aw --version' output — re-install with 'gh extension remove gh-aw && gh extension install github/gh-aw --pin v0.71.0'"
    return
  fi
  IFS='.' read -r major minor patch <<<"$raw"
  min="0.71.0"
  IFS='.' read -r min_major min_minor min_patch <<<"$min"
  if (( major < min_major )) \
     || (( major == min_major && minor < min_minor )) \
     || (( major == min_major && minor == min_minor && patch < min_patch )); then
    push_failure "gh-aw-min-version" "gh-aw v${raw} is too old (need >= v${min} for the Claude SDK 'acceptEdits' flag) — run 'gh extension remove gh-aw && gh extension install github/gh-aw --pin v${min}'"
  fi
}

check_templates_present() {
  local missing=()
  for t in "${TEMPLATES[@]}"; do
    [[ -f "$t" ]] || missing+=("$t")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    push_failure "templates-present" "Template(s) not found: ${missing[*]} — run 'tessl install jbaruch/coding-policy' first"
  fi
}

check_branch_not_local() {
  if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    push_failure "branch-not-local" "Local branch '${BRANCH}' already exists — delete with 'git branch -d ${BRANCH}' (refuses if unmerged) or rename with 'git branch -m ${BRANCH} ${BRANCH}.bak' before re-running"
  fi
}

check_branch_not_remote() {
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    push_failure "branch-not-remote" "Remote branch 'origin/${BRANCH}' already exists — delete with 'git push origin --delete ${BRANCH}' or rename before re-running"
  fi
}

# Override-mode safety check: refuse to upgrade if the consumer has dirty
# working-tree state on any path the upgrade flow can rewrite — the four
# reviewer source/lock files plus `.github/aw/actions-lock.json` (rewritten
# by `gh aw compile`) and `.gitattributes` (the LOCK_GENERATED_RULE marker
# may be appended). Mirrors how `git pull` refuses to overwrite uncommitted
# changes — forces the consumer to commit, stash, or remove the local
# content before the scaffold replaces their files. "Dirty" here covers
# three states the override could clobber:
#   - symlink at the target path (working or broken); refuse outright so
#     `cp`/compile/append never follows or replaces an unexpected link
#   - tracked file with staged or unstaged edits relative to HEAD
#   - untracked regular file at the target path (consumer hand-rolled a
#     reviewer that was never staged); without this case the override
#     would silently clobber an intentional local file.
check_no_dirty_target_edits() {
  local dirty=()
  for t in "${TARGETS[@]}"; do
    # `-e` follows symlinks, so a broken symlink (target nonexistent)
    # returns false; `-L` is true for any symlink, broken or not. The
    # OR catches every form of "something is at this path" the override
    # could clobber.
    [[ -e "$t" || -L "$t" ]] || continue
    if [[ -L "$t" ]]; then
      # Symlinks (working or broken) get their own diagnostic so the
      # consumer sees what the actual problem is — falling through to
      # the "(untracked)" branch below would mislabel a broken symlink
      # as merely untracked content. scaffold.sh refuses symlinks too;
      # this just surfaces it earlier with a clearer reason.
      dirty+=("$t (symlink target)")
    elif git ls-files --error-unmatch -- "$t" >/dev/null 2>&1; then
      # Tracked: flag if uncommitted edits exist relative to HEAD
      if ! git diff --quiet HEAD -- "$t" 2>/dev/null; then
        dirty+=("$t (uncommitted edits)")
      fi
    else
      # Untracked regular file at the target path — override would
      # silently clobber it without this case.
      dirty+=("$t (untracked)")
    fi
  done
  if [[ ${#dirty[@]} -gt 0 ]]; then
    push_failure "no-dirty-target-edits" "--override refuses to overwrite local changes in: ${dirty[*]} — commit, stash, or remove these first, then re-run"
  fi
}

main() {
  check_in_git_worktree
  check_gh_installed
  # gh-cli-dependent checks only make sense if gh is present — otherwise they
  # emit follow-on failures that can't succeed until gh is installed first.
  if command -v gh >/dev/null 2>&1; then
    check_gh_authenticated
    check_gh_aw_installed
    if gh aw --version >/dev/null 2>&1; then
      check_gh_aw_min_version
    fi
  fi
  check_templates_present
  # Remaining checks depend on a git worktree with origin; skip if either is missing
  # so we don't leak confusing git-error diagnostics on top of the real failures.
  if git rev-parse --git-dir >/dev/null 2>&1; then
    check_origin_remote
    if (( OVERRIDE_MODE == 1 )); then
      # Override mode: the upgrade branch may legitimately exist locally
      # (from a prior in-flight upgrade) or remotely (from an open
      # upgrade PR). Skip the branch-clear checks and instead refuse if
      # the consumer's working tree has uncommitted changes to the
      # target files we're about to replace.
      check_no_dirty_target_edits
    else
      # Install mode: the install branch must NOT already exist locally
      # or remotely — Step 2's overwrite refusal in the skill assumes a
      # fresh branch.
      check_branch_not_local
      if git remote get-url origin >/dev/null 2>&1; then
        check_branch_not_remote
      fi
    fi
  fi

  local failures_json
  if [[ ${#failures[@]} -eq 0 ]]; then
    failures_json='[]'
  else
    failures_json="[$(IFS=,; echo "${failures[*]}")]"
  fi

  local warnings_json
  if [[ ${#warnings[@]} -eq 0 ]]; then
    warnings_json='[]'
  else
    warnings_json="[$(IFS=,; echo "${warnings[*]}")]"
  fi

  local ok="true"
  local rc=0
  if [[ ${#failures[@]} -gt 0 ]]; then
    ok="false"
    rc=1
  fi

  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  jq -n --argjson ok "$ok" --argjson override "$override_json" --argjson failures "$failures_json" --argjson warnings "$warnings_json" \
    '{ok: $ok, override: $override, failures: $failures, warnings: $warnings}'

  # Per rules/script-delegation.md ("self-error-handling: exit non-zero on
  # failure, write a diagnostic message to stderr"), on failure also emit a
  # short diagnostic to stderr so a caller that only watches stderr notices
  # the failure rather than relying on structured-stdout parsing.
  if [[ $rc -ne 0 ]]; then
    echo "preflight: ${#failures[@]} precondition(s) failed — see the 'failures' array in stdout for recovery commands" >&2
  fi
  exit "$rc"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
