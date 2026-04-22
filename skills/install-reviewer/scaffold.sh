#!/usr/bin/env bash
# Scaffold the jbaruch/coding-policy PR review workflow into a consumer
# repo: ensure the workflows dir exists, copy the packaged template,
# compile it with gh-aw. Call this AFTER creating the feature branch
# (Step 3 of the install-reviewer skill) and BEFORE committing (Step 6).
#
# Idempotent per rules/file-hygiene.md: re-running is safe — `mkdir -p`
# no-ops if the dir exists, `cp` rewrites the source from the template,
# `gh aw compile` rewrites the lock. The overwrite-safety check lives
# in Step 2 of the install-reviewer skill, which halts before this
# script is called if prior review setup is present.
#
# If compile fails, all this script's artifacts are rolled back:
# review.md is removed, review.lock.yml is removed, and
# .github/aw/actions-lock.json is restored from a snapshot taken at
# the start (or removed if it didn't exist before). The caller never
# sees a half-scaffolded state.
#
# Usage: scaffold.sh
# Out:   one JSON object on stdout: {"source","lock","compiled"}
# Exit:  0 on success; non-zero with stderr diagnostic on failure

set -euo pipefail

TEMPLATE_SRC=".tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/review-workflow.md"
WORKFLOW_DIR=".github/workflows"
WORKFLOW_DEST="${WORKFLOW_DIR}/review.md"
WORKFLOW_LOCK="${WORKFLOW_DIR}/review.lock.yml"
ACTIONS_LOCK=".github/aw/actions-lock.json"
GITATTRIBUTES=".gitattributes"
LOCK_GENERATED_RULE='.github/workflows/*.lock.yml linguist-generated=true merge=ours'

main() {
  if [[ ! -f "$TEMPLATE_SRC" ]]; then
    echo "error: template not found at ${TEMPLATE_SRC} — run 'tessl install jbaruch/coding-policy' first" >&2
    exit 1
  fi

  # Snapshot the shared gh-aw action lockfile (if present) so compile-failure
  # rollback can restore it verbatim. Consumer repos with other gh-aw workflows
  # use this file too — losing its prior state would break their action pinning.
  local lock_snapshot=""
  if [[ -f "$ACTIONS_LOCK" ]]; then
    lock_snapshot=$(mktemp -t aw-actions-lock.XXXXXX)
    cp "$ACTIONS_LOCK" "$lock_snapshot"
  fi

  mkdir -p "$WORKFLOW_DIR"
  cp "$TEMPLATE_SRC" "$WORKFLOW_DEST"

  if ! gh aw compile review >&2; then
    rm -f "$WORKFLOW_DEST" "$WORKFLOW_LOCK"
    if [[ -n "$lock_snapshot" ]]; then
      cp "$lock_snapshot" "$ACTIONS_LOCK"
      rm -f "$lock_snapshot"
    else
      # actions-lock.json didn't exist before; if compile created it, remove it
      rm -f "$ACTIONS_LOCK"
    fi
    echo "error: 'gh aw compile review' failed — rolled back ${WORKFLOW_DEST}, ${WORKFLOW_LOCK}, and restored prior state of ${ACTIONS_LOCK}" >&2
    exit 1
  fi

  # Compile succeeded — discard the snapshot
  [[ -n "$lock_snapshot" ]] && rm -f "$lock_snapshot"

  # Ensure the lock file is marked as a generated artifact per
  # rules/file-hygiene.md. Idempotent — appends only if the exact line
  # is not already present, so existing consumer-managed .gitattributes
  # entries are not clobbered.
  if [[ ! -f "$GITATTRIBUTES" ]] || ! grep -qxF "$LOCK_GENERATED_RULE" "$GITATTRIBUTES"; then
    # If the file exists and doesn't end in a newline, add one first so the
    # appended line lands on its own row.
    if [[ -f "$GITATTRIBUTES" && -s "$GITATTRIBUTES" && -n "$(tail -c 1 "$GITATTRIBUTES")" ]]; then
      printf '\n' >> "$GITATTRIBUTES"
    fi
    printf '%s\n' "$LOCK_GENERATED_RULE" >> "$GITATTRIBUTES"
  fi

  jq -n \
    --arg source "$WORKFLOW_DEST" \
    --arg lock "$WORKFLOW_LOCK" \
    --arg gitattributes "$GITATTRIBUTES" \
    '{source: $source, lock: $lock, gitattributes: $gitattributes, compiled: true}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
