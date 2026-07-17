#!/usr/bin/env bash
# Run all install-reviewer preconditions and report them as one JSON
# result. The skill invokes this before any mutation so every preflight
# failure is surfaced together, not one-at-a-time. Checks cover: git
# worktree, GitHub CLI installation + auth, gh-aw extension, plugin
# template presence, origin remote, (mode-dependent) branch state, and a
# clean-`.env.example` guard (install mode guards `.env.example` alone;
# override mode guards it among all rewritable targets).
#
# Usage: preflight.sh [--override]
#   --override    Upgrade existing scaffolded reviewers in place (instead
#                 of failing on their existence per the install-mode
#                 safety gate). Skips the branch-not-local /
#                 branch-not-remote checks (the upgrade branch can
#                 legitimately already exist from a prior in-flight
#                 attempt) and adds a no-dirty-target-edits check
#                 covering four clobber-states the upgrade refuses to
#                 overwrite — uncommitted edits, untracked content,
#                 symlinks, and tracked deletions (HEAD-present,
#                 working-tree-removed) — so the consumer commits,
#                 stashes, restores, or removes the local content
#                 before the scaffold replaces their files.
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

# gh-aw version policy (enforced in check_gh_aw_min_version):
#   GH_AW_MIN — hard floor. `acceptEdits` (replaced bypassPermissions) and
#               `engine.args` both require >= 0.71.0; older compiles produce
#               lock files current Claude SDK versions reject.
#   GH_AW_PIN — version the install/recovery commands pin to, for
#               reproducibility. Tracks the latest gh-aw stable release,
#               which now sits above the floor (stable used to lag below it,
#               which is why a prerelease pin was once required).
GH_AW_MIN="0.71.0"
GH_AW_PIN="0.81.6"

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
# Capture stderr, not `2>/dev/null`: git returns 128 for BOTH the ordinary
# not-a-repo case AND real faults (corrupt/unreadable repo, an unsafe-
# ownership refusal), so the exit code alone cannot tell them apart — the
# distinguisher is git's own message. `rev-parse --show-toplevel` on no repo
# emits the stable, decades-old sentinel "not a git repository"; any other
# 128 is a fault this preflight must surface, not treat as "just not in a
# repo". Matching that one fixed token is not the regex-trap
# (rules/script-delegation.md) — it is a documented tool sentinel, not
# free-form text.
repo_root=""
git_rc=0
git_err=""
repo_root=$(git rev-parse --show-toplevel 2>/tmp/preflight_git_err.$$) || git_rc=$?
git_err=$(cat /tmp/preflight_git_err.$$ 2>/dev/null); rm -f /tmp/preflight_git_err.$$
# rc 128 whose stderr carries the not-a-repo sentinel is the expected case:
# fall through, and check_in_git_worktree below reports it as structured
# JSON. Every other non-zero rc — and a 128 WITHOUT that sentinel — is a
# git fault surfaced here.
if [[ $git_rc -ne 0 ]] && ! { [[ $git_rc -eq 128 ]] && [[ "$git_err" == *"not a git repository"* ]]; }; then
  # Build the JSON with jq, never interpolation: `reason` embeds raw git
  # stderr, which can carry quotes/newlines that would break the parse (the
  # push_failure injection class). jq is guaranteed here — the missing-jq
  # guard near the top of this file exits before we reach this line — so the
  # documented single-JSON-object contract holds (rules/script-delegation.md).
  override_bool="false"
  (( OVERRIDE_MODE == 1 )) && override_bool="true"
  reason="git rev-parse --show-toplevel failed (rc=${git_rc}): ${git_err} — not the ordinary not-a-repo case; verify git is installed and the repository is readable (e.g. 'git status', check ownership/safe.directory), then re-run"
  jq -nc --argjson override "$override_bool" --arg reason "$reason" \
    '{ok: false, override: $override, failures: [{check: "git-usable", reason: $reason}], warnings: []}'
  echo "preflight.sh: ${reason}" >&2
  exit 2
fi
if [[ -n "$repo_root" ]]; then
  cd "$repo_root"
fi

if (( OVERRIDE_MODE == 1 )); then
  BRANCH="feat/upgrade-coding-policy-review"
else
  BRANCH="feat/add-coding-policy-review"
fi
TEMPLATE_DIR=".tessl/plugins/jbaruch/coding-policy/skills/install-reviewer"
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
  ".env.example"
)

declare -a failures=()
declare -a warnings=()

# Build the JSON object with jq, never string interpolation: a reason can
# carry raw command output (a version string, a git error), and a quote,
# backslash, or newline in it would break the `jq --argjson failures` parse
# at emit time and drop the whole documented JSON contract. jq escapes it.
push_failure() {
  failures+=("$(jq -nc --arg check "$1" --arg reason "$2" '{check: $check, reason: $reason}')")
}

push_warning() {
  warnings+=("$(jq -nc --arg check "$1" --arg reason "$2" '{check: $check, reason: $reason}')")
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
    push_failure "gh-aw-installed" "gh-aw extension missing — run 'gh extension install github/gh-aw --pin v${GH_AW_PIN}'"
}

# Floor and pin are defined at the top of the script (GH_AW_MIN / GH_AW_PIN).
# We enforce a >= GH_AW_MIN floor but tell users to install GH_AW_PIN (the
# latest stable), which clears the floor and keeps installs reproducible.
check_gh_aw_min_version() {
  local raw min major minor patch min_major min_minor min_patch
  # Run the command, THEN parse its captured output — never one pipeline.
  # `pipefail` reports the RIGHTMOST non-zero status, so piping straight
  # into grep hides the command's own failure behind grep's exit 1: a
  # missing `gh` (127) plus a no-match grep (1) yields 1, indistinguishable
  # from "gh ran and printed no version", and the user gets told to
  # re-install the gh-aw extension when the real problem is that `gh` is
  # not on PATH at all (rules/error-handling.md — distinguish an expected
  # non-result from a tool failure; Actionable Messages).
  #
  # `gh aw --version` writes to stderr (typical gh-extension idiom), so
  # merge streams into the capture rather than discarding stderr.
  local gh_out gh_rc=0
  gh_out=$(gh aw --version 2>&1) || gh_rc=$?
  if [[ $gh_rc -ne 0 ]]; then
    push_failure "gh-aw-min-version" "Could not run 'gh aw --version' (rc=${gh_rc}): ${gh_out} — verify the gh CLI is installed and on PATH ('command -v gh') and the gh-aw extension is installed ('gh extension list'), then re-run"
    return
  fi

  # Parse the captured output. grep's exit 1 here means the command ran and
  # printed no version — a real preflight finding, reported below.
  #
  # No pipeline at all — a here-string. Under `pipefail` a pipeline reports
  # the rightmost NON-ZERO status, so any pipe here lets a process other than
  # grep speak for the parse:
  #   `grep | head -n1` — head closes the pipe on a still-writing grep, grep
  #     dies with SIGPIPE (141), and 141 > 1 fires the tool-fault branch on a
  #     SUCCESSFUL parse.
  #   `printf | grep -m1` — the same race one process upstream: grep -m1 exits
  #     at the first match and closes the pipe on a still-writing printf, so
  #     printf carries the 141 while grep exits 0.
  # Both reproduced at 200k matches. A here-string has no second process, so
  # grep's own rc is the only status the `case` can read.
  local grep_rc=0
  raw=$(grep -m1 -oE '[0-9]+\.[0-9]+\.[0-9]+' <<<"$gh_out") || grep_rc=$?
  if [[ $grep_rc -gt 1 ]]; then
    push_failure "gh-aw-min-version" "Version parse failed (grep rc=${grep_rc}) while reading 'gh aw --version' output — this is a fault in preflight.sh's parse, not a gh-aw problem; report it with the output that triggered it: ${gh_out}"
    return
  fi
  if [[ -z "$raw" ]]; then
    push_failure "gh-aw-min-version" "Could not parse 'gh aw --version' output — re-install with 'gh extension remove gh-aw && gh extension install github/gh-aw --pin v${GH_AW_PIN}'"
    return
  fi
  IFS='.' read -r major minor patch <<<"$raw"
  min="$GH_AW_MIN"
  IFS='.' read -r min_major min_minor min_patch <<<"$min"
  if (( major < min_major )) \
     || (( major == min_major && minor < min_minor )) \
     || (( major == min_major && minor == min_minor && patch < min_patch )); then
    push_failure "gh-aw-min-version" "gh-aw v${raw} is too old (need >= v${min} for the Claude SDK 'acceptEdits' flag) — run 'gh extension remove gh-aw && gh extension install github/gh-aw --pin v${GH_AW_PIN}'"
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
    push_failure "branch-not-local" "Local branch '${BRANCH}' already exists — delete with: git branch -d '${BRANCH}' (refuses if unmerged); or rename with: git branch -m '${BRANCH}' '${BRANCH}.bak' before re-running"
  fi
}

check_branch_not_remote() {
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    push_failure "branch-not-remote" "Remote branch 'origin/${BRANCH}' already exists — delete with 'git push origin --delete ${BRANCH}' or rename before re-running"
  fi
}

# Override-mode safety check: refuse to upgrade if the consumer has dirty
# working-tree state on any path the upgrade flow can rewrite OR stage —
# the four reviewer source/lock files, `.github/aw/actions-lock.json`
# (rewritten by `gh aw compile`), `.gitattributes` (the LOCK_GENERATED_RULE
# marker may be appended), and `.env.example` (the reviewer secrets block
# may be appended). `.env.example` is merge-not-overwrite, so scaffold
# itself preserves consumer content — but commit.sh stages the whole file,
# so a consumer's unrelated pending `.env.example` edits would otherwise be
# swept into the reviewer-upgrade commit. A never-tracked, not-yet-created
# `.env.example` (fresh install/upgrade) has nothing to clobber and is not
# flagged. Mirrors how `git pull` refuses to overwrite uncommitted changes
# — forces the consumer to commit, stash, or remove the local content
# before the scaffold replaces their files. "Dirty" here covers four
# states the override could clobber:
#   - symlink at the target path (working or broken); refuse outright so
#     `cp`/compile/append never follows or replaces an unexpected link
#   - tracked file with staged or unstaged edits relative to HEAD
#   - untracked regular file at the target path (consumer hand-rolled a
#     reviewer that was never staged); without this case the override
#     would silently clobber an intentional local file
#   - tracked file deleted from the working tree (`rm` or `git rm`) but
#     still present at HEAD; without this case scaffold.sh re-creates
#     the file and silently clobbers the consumer's intentional removal.
# Classify a single target's working-tree state, echoing a short reason
# when it is one the scaffold/commit flow could clobber or wrongly stage,
# and nothing for a clean tracked file or a path that neither exists nor
# is tracked at HEAD. Pure inspection — no mutation, always exits 0.
classify_target_dirty() {
  local t="$1"
  # `-e` follows symlinks, so a broken symlink (target nonexistent)
  # returns false; `-L` is true for any symlink, broken or not. The OR
  # catches every form of "something is at this path".
  if [[ ! -e "$t" && ! -L "$t" ]]; then
    # Nothing at this path in the working tree. If HEAD still tracks one
    # there the consumer either `rm`'d it (missing from working tree,
    # present in index + HEAD) or `git rm`'d it (missing from working tree
    # AND index, still in HEAD). `git diff --diff-filter=D HEAD` catches
    # both because it compares HEAD against the working tree. `--quiet`
    # exits 0 when there's no diff and 1 when there is. Branch on the code:
    # 1 is "deleted vs HEAD" (the finding), but >1 is a real git fault, and
    # `if ! ...` would collapse both and mislabel the fault "tracked
    # deletion" (rules/error-handling.md — distinguish an expected
    # non-result from a tool failure).
    local d_rc=0
    git diff --quiet --diff-filter=D HEAD -- "$t" 2>/dev/null || d_rc=$?
    case "$d_rc" in
      0) ;;                       # no diff: not deleted
      1) echo "tracked deletion" ;;
      *) echo "git-error (git diff rc=${d_rc} on ${t})" ;;
    esac
    return 0
  fi
  if [[ -L "$t" ]]; then
    # Symlinks (working or broken) get their own diagnostic — falling
    # through to "untracked" would mislabel a broken symlink. scaffold.sh
    # refuses symlinks too; this just surfaces it earlier.
    echo "symlink target"
  elif git ls-files --error-unmatch -- "$t" >/dev/null 2>&1; then
    # Tracked: flag uncommitted edits vs HEAD. Same rc branch as the
    # deletion probe above — 1 is dirty (the finding), >1 is a git fault
    # that `if ! ...` would mislabel "uncommitted edits".
    local e_rc=0
    git diff --quiet HEAD -- "$t" 2>/dev/null || e_rc=$?
    case "$e_rc" in
      0) ;;                       # clean
      1) echo "uncommitted edits" ;;
      *) echo "git-error (git diff rc=${e_rc} on ${t})" ;;
    esac
  else
    # Untracked regular file at the target path.
    echo "untracked"
  fi
}

check_no_dirty_target_edits() {
  local dirty=() t reason
  for t in "${TARGETS[@]}"; do
    reason=$(classify_target_dirty "$t")
    [[ -n "$reason" ]] && dirty+=("$t ($reason)")
  done
  if [[ ${#dirty[@]} -gt 0 ]]; then
    push_failure "no-dirty-target-edits" "--override refuses to overwrite local changes in: ${dirty[*]} — commit, stash, restore, or remove these first, then re-run"
  fi
}

# Install-mode guard for `.env.example`. The six reviewer targets are
# guarded in install mode by the skill's Step 2 existence-refusal, but
# `.env.example` legitimately pre-exists in many repos and commit.sh
# stages it wholesale — so a dirty, untracked, symlinked, or tracked-
# deleted `.env.example` would otherwise sweep unrelated local content
# (possibly real secret values) into the reviewer-install commit. A
# clean tracked file or an absent one is fine — scaffold merges into the
# former and creates the latter, and commit.sh stages only the diff.
check_env_example_clean() {
  local reason
  reason=$(classify_target_dirty ".env.example")
  if [[ -n "$reason" ]]; then
    push_failure "env-example-not-clean" ".env.example is in a state install cannot safely stage (${reason}) — install stages it into the reviewer PR, which could commit unrelated local content (possibly real secret values). Commit, stash, restore, or remove it first, then re-run"
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
      # fresh branch. The reviewer targets are guarded by that refusal,
      # but `.env.example` can pre-exist, so guard it against dirty/
      # untracked state commit.sh would otherwise stage wholesale.
      check_branch_not_local
      if git remote get-url origin >/dev/null 2>&1; then
        check_branch_not_remote
      fi
      check_env_example_clean
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
