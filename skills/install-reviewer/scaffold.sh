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
# If compile fails, the partially-written artifacts are removed so the
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

  mkdir -p "$WORKFLOW_DIR"
  cp "$TEMPLATE_SRC" "$WORKFLOW_DEST"

  if ! gh aw compile review >&2; then
    # Roll back this workflow's artifacts so the repo isn't left half-scaffolded.
    # Do NOT delete .github/aw/actions-lock.json — it may be shared with other
    # gh-aw workflows in the consumer repo, and removing it would break their
    # action pinning. The caller should inspect actions-lock.json diff manually
    # if they care about it.
    rm -f "$WORKFLOW_DEST" "$WORKFLOW_LOCK"
    echo "error: 'gh aw compile review' failed — rolled back ${WORKFLOW_DEST} and ${WORKFLOW_LOCK}; .github/aw/actions-lock.json left untouched (may be shared)" >&2
    exit 1
  fi

  jq -n \
    --arg source "$WORKFLOW_DEST" \
    --arg lock "$WORKFLOW_LOCK" \
    '{source: $source, lock: $lock, compiled: true}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
