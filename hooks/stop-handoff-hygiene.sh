#!/usr/bin/env bash
# Flag leftover local hygiene before the agent hands off (a Stop gate).
#
# A Claude Code `Stop` hook: at handoff it runs deterministic, universal
# git-hygiene checks and, when it finds clearly-actionable leftovers, blocks the
# stop once with an actionable reason so the agent can clean up. We hand-cleaned
# exactly this mess in real sessions — stray merged-and-remote-deleted local
# branches and an abandoned worktree. This mechanizes the pre-handoff cleanup
# rules/language-diagnostics.md endorses ("a Stop or pre-handoff hook running the
# gate mechanizes this") and complements the fleet-wide delete_branch_on_merge
# (that auto-cleans REMOTE branches; this covers the LOCAL branches/worktrees it
# never touches).
#
# Why nativeHooks.claude-code (not the portable `hooks` tier): blocking a stop is
# an agent-specific contract with no portable consensus form — Tessl translates
# `additionalContext` (informational), not a stop-block. This hook emits Claude
# Code's native Stop decision, so it ships under nativeHooks.claude-code.
#
# Blocking findings (gate the stop, once):
#   - Leftover local branches whose upstream is gone (merged then remote-deleted).
#   - Orphaned linked worktrees whose branch's upstream is gone.
#   - Diagnostics findings in the CHANGED set only (uncommitted .sh/.py), via the
#     bundled scripts/run-diagnostics.sh --files — skipped when nothing lintable
#     changed, so a clean handoff costs nothing.
# Report-only (never blocks on its own): a dirty working tree — often intentional
#   work-in-progress, surfaced to the user but not trapped.
#
# Loop-safe: reads `stop_hook_active` from stdin and allows immediately when set,
# so it blocks at most once per handoff chain — never traps the agent in a loop.
# Fail-open: any inability to evaluate (no jq, not a git repo, parse failure)
# allows the stop. A hygiene nudge must never wedge a handoff.
#
# Contract:
#   stdin : Claude Code Stop JSON. Only `.stop_hook_active` is read.
#   stdout: on a block, one JSON object {"decision":"block","reason":"<text>"};
#           otherwise nothing.
#   exit  : always 0 (block is expressed in stdout JSON, never via exit code).
#           Best-effort failures warn to stderr and allow the stop.
#   state : none — every check reads live git state.
set -euo pipefail

warn() { printf 'stop-handoff-hygiene: %s\n' "$1" >&2; }

# Membership test without associative arrays (macOS ships bash 3.2).
in_list() { # <needle> <haystack...>
  local needle="$1" x; shift
  for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done
  return 1
}

main() {
  local input active inside
  local -a gone_branches=() wt_paths=() wt_branches=() leftover=() orphaned=() changed=()
  local -a blocking=() reports=()

  # jq is required to read stop_hook_active and to emit the block JSON safely.
  # Without it we cannot evaluate loop-safety, so fail open (allow the stop).
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found — install jq to enable the handoff-hygiene gate; allowing stop"
    return 0
  fi

  # Read the Stop payload and honor the loop guard: if this stop is already the
  # result of our prior block, allow it — never block twice (no loop).
  input="$(cat)" || input=""
  active="$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" || active="parse-error"
  [[ "$active" == "true" ]] && return 0
  [[ "$active" == "parse-error" ]] && { warn "could not parse Stop payload — allowing stop"; return 0; }

  command -v git >/dev/null 2>&1 || { warn "git not found — allowing stop"; return 0; }

  # Outside a work tree there is nothing to check. `--is-inside-work-tree` exits
  # 128 ("not a git repository") outside a repo — the common non-repo session, an
  # expected non-result whose diagnostic we silence deliberately. Proceed only on
  # a literal "true"; a bare repo / gitdir ("false") and the non-repo case both
  # allow silently. A Stop gate must never wedge or spam a non-repo handoff.
  inside="$(git rev-parse --is-inside-work-tree 2>/dev/null)" || inside="__notrepo__"
  case "$inside" in
    true) : ;;
    *) return 0 ;;
  esac

  collect_gone_branches
  collect_worktrees

  # Partition gone branches: those checked out in a linked worktree are reported
  # as orphaned worktrees (remove the worktree); the rest as leftover branches.
  local b p i
  for b in "${gone_branches[@]}"; do
    in_list "$b" "${wt_branches[@]}" || leftover+=("$b")
  done
  for (( i = 0; i < ${#wt_paths[@]}; i++ )); do
    b="${wt_branches[$i]}"; p="${wt_paths[$i]}"
    if in_list "$b" "${gone_branches[@]}"; then orphaned+=("${p} (branch ${b})"); fi
  done

  build_branch_findings
  run_changed_diagnostics
  check_dirty_tree

  if (( ${#blocking[@]} > 0 )); then
    emit_block
  elif (( ${#reports[@]} > 0 )); then
    # Report-only: surface to the user (stderr) but do not block the handoff.
    local r
    for r in "${reports[@]}"; do warn "$r"; done
  fi
  return 0
}

# Populate gone_branches with local branches whose upstream is [gone].
collect_gone_branches() {
  local name track
  while IFS=$'\t' read -r name track; do
    [[ "$track" == "[gone]" ]] && gone_branches+=("$name")
  done < <(git for-each-ref --format='%(refname:short)%09%(upstream:track)' refs/heads)
  return 0   # a while-read's EOF status is non-zero; don't let it abort under set -e
}

# Populate wt_paths/wt_branches for LINKED worktrees only (the first porcelain
# record is the main worktree and is skipped). Detached worktrees have no branch
# and are skipped.
collect_worktrees() {
  local line key val cur_path="" cur_branch="" first=1
  flush() {
    if (( first )); then first=0
    elif [[ -n "$cur_branch" ]]; then wt_paths+=("$cur_path"); wt_branches+=("$cur_branch"); fi
    cur_path=""; cur_branch=""
  }
  while IFS= read -r line; do
    if [[ -z "$line" ]]; then flush; continue; fi
    key="${line%% *}"; val="${line#* }"
    case "$key" in
      worktree) cur_path="$val" ;;
      branch)   cur_branch="${val#refs/heads/}" ;;
    esac
  done < <(git worktree list --porcelain)
  [[ -n "$cur_path" ]] && flush   # flush a trailing record with no blank line
  return 0
}

# Turn leftover branches and orphaned worktrees into blocking-finding sections.
build_branch_findings() {
  local current b p section
  current="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || current=""
  if (( ${#leftover[@]} > 0 )); then
    section="Leftover local branches (merged, upstream deleted) — delete them:"
    for b in "${leftover[@]}"; do
      if [[ "$b" == "$current" ]]; then
        section+=$'\n'"  - ${b} (currently checked out — switch away first): git switch <other> && git branch -d ${b}"
      else
        section+=$'\n'"  - ${b}: git branch -d ${b}"
      fi
    done
    blocking+=("$section")
  fi
  if (( ${#orphaned[@]} > 0 )); then
    section="Orphaned worktrees (branch merged, upstream deleted) — remove them:"
    for p in "${orphaned[@]}"; do
      section+=$'\n'"  - ${p}: git worktree remove <path> && git branch -d <branch>"
    done
    blocking+=("$section")
  fi
  return 0
}

# Diagnostics on the CHANGED set only: uncommitted .sh/.py files. Skips silently
# when nothing lintable changed. run-diagnostics.sh --files exits 1 on findings
# (block), 2 on setup error (engine missing — warn, don't block).
run_changed_diagnostics() {
  collect_changed_lintable
  (( ${#changed[@]} > 0 )) || return 0

  local diag="${BASH_SOURCE[0]%/*}/../scripts/run-diagnostics.sh" out drc=0
  if [[ ! -f "$diag" ]]; then
    warn "bundled run-diagnostics.sh not found at ${diag} — skipping the diagnostics check"
    return 0
  fi
  out="$(bash "$diag" --files "${changed[@]}" 2>&1)" || drc=$?
  if (( drc == 1 )); then
    blocking+=("Diagnostics findings in changed files — fix before handoff:"$'\n'"${out}")
  elif (( drc == 2 )); then
    warn "diagnostics engines unavailable (run-diagnostics exit 2) — install shellcheck/pyright to enable the check; not blocking"
  fi
  return 0
}

# Populate `changed` with uncommitted .sh/.py files: tracked changes vs HEAD (or
# the index in an unborn repo) plus untracked files, NUL-safe.
collect_changed_lintable() {
  local f
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    while IFS= read -r -d '' f; do changed+=("$f"); done \
      < <(git diff --name-only -z --diff-filter=ACMR HEAD -- '*.sh' '*.py')
  else
    while IFS= read -r -d '' f; do changed+=("$f"); done \
      < <(git diff --name-only -z --cached --diff-filter=ACMR -- '*.sh' '*.py')
  fi
  while IFS= read -r -d '' f; do changed+=("$f"); done \
    < <(git ls-files -z --others --exclude-standard -- '*.sh' '*.py')
  return 0
}

# A dirty working tree is report-only — often intentional WIP, never blocks alone.
check_dirty_tree() {
  local status
  status="$(git status --porcelain)" || return 0
  [[ -n "$status" ]] && reports+=("Working tree has uncommitted changes — commit, stash, or discard before handoff.")
  return 0
}

# Emit Claude Code's native Stop block decision with the assembled reason.
emit_block() {
  local reason section
  reason="Pre-handoff hygiene — resolve these local leftovers before finishing:"
  for section in "${blocking[@]}"; do reason+=$'\n\n'"${section}"; done
  if (( ${#reports[@]} > 0 )); then
    local r
    for r in "${reports[@]}"; do reason+=$'\n\n'"${r}"; done
  fi
  jq -n --arg r "$reason" '{decision: "block", reason: $r}' ||
    warn "could not emit the block decision as JSON — allowing stop"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
