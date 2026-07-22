#!/usr/bin/env bash
# Copy the fleet-reviewer opt-in files into the consumer repo:
#   .github/fleet-review-enabled            — opt-in marker; the central
#                                             coding-policy-fleet-reviewer App polls
#                                             and reviews every repo carrying it
#   .github/workflows/review-trigger.yml    — PR-time trigger; fires an immediate
#                                             single-PR review so the verdict lands
#                                             before merge (the poll is a backstop)
#   .github/copilot-instructions.md         — the Copilot complementary lane
#
# The Codex credential lives only in coding-policy. The consumer holds the marker,
# the thin trigger workflow, and one stable FLEET_DISPATCH_TOKEN (a narrow PAT).
#
# Every target is snapshotted before any write and restored if a later copy
# fails, so a partial run never leaves a half-written reviewer.
#
# Usage: scaffold.sh [--override]
#   --override   Upgrade mode — overwrite existing targets. Install mode refuses
#                if any target already exists (the skill's Step 2 gates that).
# Out:   one JSON object on stdout:
#          {"state":"scaffolded|no-op","override":bool,
#           "files":[{"target":"...","action":"created|overwritten|unchanged"}]}
# Exit:  0 on success (including no-op); non-zero with a stderr diagnostic on
#        failure (targets restored to their prior contents first).

set -euo pipefail

TEMPLATE_DIR=".tessl/plugins/jbaruch/coding-policy/skills/install-reviewer/templates"
# "<source relative to TEMPLATE_DIR>:<target in the consumer repo>"
# The marker and the trigger source carry a `.md` shim extension because tessl
# packaging ships only .md/.sh/.json/.py — a `.yml` or extensionless template is
# dropped from the installed plugin, so scaffold would find no source. Keep the
# `.md` suffix; the target names below are what the consumer actually gets.
MANIFEST=(
  "fleet-review-enabled.md:.github/fleet-review-enabled"
  "review-trigger.yml.md:.github/workflows/review-trigger.yml"
  "copilot-instructions.md:.github/copilot-instructions.md"
)

main() {
  local OVERRIDE_MODE=0 arg
  for arg in "$@"; do
    case "$arg" in
      --override) OVERRIDE_MODE=1 ;;
      *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
    esac
  done

  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; exit 2; }

  local repo_root
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null) \
    || { echo "error: not inside a git worktree — run from within the consumer repo" >&2; exit 1; }
  cd "$repo_root"

  local pair src tgt
  for pair in "${MANIFEST[@]}"; do
    src="${TEMPLATE_DIR}/${pair%%:*}"
    [[ -f "$src" ]] || { echo "error: template not found: $src — run 'tessl install jbaruch/coding-policy' first" >&2; exit 1; }
  done

  # Refuse symlink targets; in install mode refuse pre-existing targets.
  for pair in "${MANIFEST[@]}"; do
    tgt="${pair#*:}"
    [[ -L "$tgt" ]] && { echo "error: ${tgt} is a symlink — refusing to write through it; replace it with a regular file (or remove it) and re-run" >&2; exit 1; }
    if [[ -e "$tgt" && ! -f "$tgt" ]]; then
      echo "error: ${tgt} exists but is not a regular file (directory, FIFO, or device) — refusing to write; remove it and re-run" >&2
      exit 1
    fi
    if (( OVERRIDE_MODE == 0 )) && [[ -e "$tgt" ]]; then
      echo "error: ${tgt} already exists — refusing to overwrite in install mode; re-run in upgrade mode (--override) to refresh" >&2
      exit 1
    fi
  done

  # Snapshot existing targets for rollback. snap_existed[i] tracks each.
  local SNAP_DIR i=0
  SNAP_DIR=$(mktemp -d) || { echo "error: mktemp -d failed — check TMPDIR is writable" >&2; exit 1; }
  local -a snap_existed=()
  for pair in "${MANIFEST[@]}"; do
    tgt="${pair#*:}"
    if [[ -f "$tgt" ]]; then cp "$tgt" "${SNAP_DIR}/$i"; snap_existed[i]=1; else snap_existed[i]=0; fi
    i=$((i + 1))
  done

  # Best-effort rollback under `set +e` (rules/error-handling.md — warn, never nothing).
  restore() {
    local j=0 p t
    for p in "${MANIFEST[@]}"; do
      t="${p#*:}"
      if (( snap_existed[j] == 1 )); then
        cp "${SNAP_DIR}/$j" "$t" || echo "scaffold.sh: warning: could not restore ${t} — restore it by hand" >&2
      else
        rm -f "$t" || echo "scaffold.sh: warning: could not remove ${t} during rollback — remove it by hand" >&2
      fi
      j=$((j + 1))
    done
  }
  on_err() {
    local rc=$?
    trap - ERR
    set +e
    restore
    echo "error: scaffold failed (rc=${rc}) — targets restored to their prior contents" >&2
    rm -rf "$SNAP_DIR" || echo "scaffold.sh: warning: could not remove temp dir ${SNAP_DIR} — remove it by hand" >&2
    exit "$rc"
  }
  trap on_err ERR

  # Copy each template to its target.
  local results="[]" action
  i=0
  for pair in "${MANIFEST[@]}"; do
    src="${TEMPLATE_DIR}/${pair%%:*}"; tgt="${pair#*:}"
    mkdir -p "$(dirname "$tgt")"
    action="created"
    (( snap_existed[i] == 1 )) && action="overwritten"
    cp "$src" "$tgt"
    case "$tgt" in *.sh) chmod +x "$tgt" ;; esac
    if (( snap_existed[i] == 1 )) && cmp -s "$tgt" "${SNAP_DIR}/$i"; then action="unchanged"; fi
    results=$(jq -c --arg t "$tgt" --arg a "$action" '. + [{target:$t, action:$a}]' <<<"$results")
    i=$((i + 1))
  done

  local state="scaffolded"
  jq -e 'all(.[]; .action=="unchanged")' <<<"$results" >/dev/null && state="no-op"

  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  trap - ERR
  rm -rf "$SNAP_DIR"

  jq -n --arg state "$state" --argjson override "$override_json" --argjson files "$results" \
    '{state: $state, override: $override, files: $files}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
