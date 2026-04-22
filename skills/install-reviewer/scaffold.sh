#!/usr/bin/env bash
# Scaffold the jbaruch/coding-policy PR review workflow into a consumer
# repo: ensure the workflows dir exists, copy the packaged template,
# compile it with gh-aw. Call this AFTER creating the feature branch
# (Step 3 of the install-reviewer skill) and BEFORE committing (Step 6).
# If compile fails, the partially-written source is removed so the
# caller never sees a half-scaffolded state.
#
# Usage: scaffold.sh
# Out:   one JSON object on stdout: {"source","lock","compiled"}
# Exit:  0 on success; non-zero with stderr diagnostic on failure

set -euo pipefail

TEMPLATE_SRC=".tessl/tiles/jbaruch/coding-policy/skills/install-reviewer/review-workflow.md"
WORKFLOW_DIR=".github/workflows"
WORKFLOW_DEST="${WORKFLOW_DIR}/review.md"
WORKFLOW_LOCK="${WORKFLOW_DIR}/review.lock.yml"

main() {
  if [[ ! -f "$TEMPLATE_SRC" ]]; then
    echo "error: template not found at ${TEMPLATE_SRC} — run 'tessl install jbaruch/coding-policy' first" >&2
    exit 1
  fi
  if [[ -f "$WORKFLOW_DEST" || -f "$WORKFLOW_LOCK" ]]; then
    echo "error: existing review workflow files detected (${WORKFLOW_DEST} or ${WORKFLOW_LOCK}); refusing to overwrite — Step 2 of the skill should have caught this" >&2
    exit 1
  fi

  mkdir -p "$WORKFLOW_DIR"
  cp "$TEMPLATE_SRC" "$WORKFLOW_DEST"

  if ! gh aw compile review >&2; then
    # Roll back the source write so the repo isn't left half-scaffolded.
    rm -f "$WORKFLOW_DEST"
    echo "error: 'gh aw compile review' failed — rolled back ${WORKFLOW_DEST}" >&2
    exit 1
  fi

  jq -n \
    --arg source "$WORKFLOW_DEST" \
    --arg lock "$WORKFLOW_LOCK" \
    '{source: $source, lock: $lock, compiled: true}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
