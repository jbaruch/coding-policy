#!/usr/bin/env bash
# Write the two install-reviewer artifacts from the packaged templates:
#
#   AGENTS.md                       — a marker-delimited `## Review guidelines`
#                                     block steering the Codex code-review app.
#   .github/copilot-instructions.md — the Copilot complementary-lane charter.
#
# The AGENTS.md block is delimited by
#   <!-- BEGIN jbaruch/coding-policy review guidelines -->
#   <!-- END   jbaruch/coding-policy review guidelines -->
# so the block is created, appended, or replaced in place across re-runs
# WITHOUT disturbing any other AGENTS.md content the consumer maintains.
# .github/copilot-instructions.md is written wholesale (install mode refuses
# a pre-existing one via the skill's Step 2; upgrade mode overwrites it).
#
# Both targets are snapshotted before any write and restored if a later step
# fails, so a partial run never leaves half-written artifacts.
#
# Usage: scaffold.sh [--override]
#   --override    Upgrade mode. Same writes; the mode is echoed in the output.
# Out:   one JSON object on stdout:
#          {"state": "scaffolded|no-op", "override": bool,
#           "agents_action": "created|appended|replaced|unchanged",
#           "copilot_action": "created|overwritten|unchanged"}
# Exit:  0 on success (including no-op); non-zero with a stderr diagnostic on
#        failure (targets restored to their prior contents first).

set -euo pipefail

BEGIN_MARKER="<!-- BEGIN jbaruch/coding-policy review guidelines -->"
END_MARKER="<!-- END jbaruch/coding-policy review guidelines -->"
TEMPLATE_DIR=".tessl/plugins/jbaruch/coding-policy/skills/install-reviewer"
AGENTS_TEMPLATE="${TEMPLATE_DIR}/AGENTS_REVIEW_GUIDELINES.md"
COPILOT_TEMPLATE="${TEMPLATE_DIR}/copilot-instructions.md"
AGENTS_TARGET="AGENTS.md"
COPILOT_TARGET=".github/copilot-instructions.md"

main() {
  local OVERRIDE_MODE=0 arg
  for arg in "$@"; do
    case "$arg" in
      --override) OVERRIDE_MODE=1 ;;
      *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
    esac
  done

  if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
    exit 2
  fi

  local repo_root
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "error: not inside a git worktree — run from within the consumer repo" >&2
    exit 1
  }
  cd "$repo_root"

  local t
  for t in "$AGENTS_TEMPLATE" "$COPILOT_TEMPLATE"; do
    if [[ ! -f "$t" ]]; then
      echo "error: template not found: $t — run 'tessl install jbaruch/coding-policy' first" >&2
      exit 1
    fi
  done

  # Refuse symlink targets outright — never follow or replace an unexpected
  # link (the same guard preflight.sh surfaces earlier).
  for t in "$AGENTS_TARGET" "$COPILOT_TARGET"; do
    if [[ -L "$t" ]]; then
      echo "error: ${t} is a symlink — refusing to write through it; replace it with a regular file (or remove it) and re-run" >&2
      exit 1
    fi
  done

  # Snapshot each target's prior state so a failure after the first write
  # restores the tree to how we found it. These locals are visible to the
  # ERR-trap handler below via bash dynamic scoping while main is on the stack.
  local SNAP_DIR agents_existed=0 copilot_existed=0
  SNAP_DIR=$(mktemp -d) || { echo "error: mktemp -d failed — check TMPDIR is writable" >&2; exit 1; }
  [[ -f "$AGENTS_TARGET"  ]] && { cp "$AGENTS_TARGET"  "${SNAP_DIR}/agents";  agents_existed=1; }
  [[ -f "$COPILOT_TARGET" ]] && { cp "$COPILOT_TARGET" "${SNAP_DIR}/copilot"; copilot_existed=1; }

  # Best-effort rollback: it runs from on_err under `set +e`, so a failing
  # cp/rm must warn rather than pass silently (rules/error-handling.md — best-
  # effort work that continues past a failure emits a warning to stderr).
  restore() {
    if (( agents_existed == 1 )); then
      cp "${SNAP_DIR}/agents" "$AGENTS_TARGET" || echo "scaffold.sh: warning: could not restore ${AGENTS_TARGET} from ${SNAP_DIR}/agents — restore it by hand" >&2
    else
      rm -f "$AGENTS_TARGET" || echo "scaffold.sh: warning: could not remove ${AGENTS_TARGET} during rollback — remove it by hand" >&2
    fi
    if (( copilot_existed == 1 )); then
      cp "${SNAP_DIR}/copilot" "$COPILOT_TARGET" || echo "scaffold.sh: warning: could not restore ${COPILOT_TARGET} from ${SNAP_DIR}/copilot — restore it by hand" >&2
    else
      rm -f "$COPILOT_TARGET" || echo "scaffold.sh: warning: could not remove ${COPILOT_TARGET} during rollback — remove it by hand" >&2
    fi
  }
  on_err() {
    local rc=$?
    # Disarm the ERR trap and errexit first so a failing cp/rm inside restore
    # can't re-enter this handler or abort before the original error is
    # reported — rollback is best-effort.
    trap - ERR
    set +e
    restore
    echo "error: scaffold failed (rc=${rc}) — targets restored to their prior contents" >&2
    rm -rf "$SNAP_DIR" || echo "scaffold.sh: warning: could not remove temp dir ${SNAP_DIR} — remove it by hand" >&2
    exit "$rc"
  }
  # Armed only AFTER the snapshot so a pre-snapshot failure can't restore from
  # an empty dir.
  trap on_err ERR

  # --- AGENTS.md: create / append / replace the marked block -------------
  local agents_action tmp
  if [[ ! -f "$AGENTS_TARGET" ]]; then
    cp "$AGENTS_TEMPLATE" "$AGENTS_TARGET"
    agents_action="created"
  elif grep -qF "$BEGIN_MARKER" "$AGENTS_TARGET"; then
    # A BEGIN marker with no matching END would make the awk rewrite below drop
    # everything after BEGIN — refuse rather than destroy consumer content.
    if ! grep -qF "$END_MARKER" "$AGENTS_TARGET"; then
      echo "error: ${AGENTS_TARGET} has the BEGIN marker but no END marker — refusing to rewrite (that would drop everything after BEGIN); repair the file by hand and re-run" >&2
      rm -rf "$SNAP_DIR"
      exit 1
    fi
    # Replace the BEGIN..END range with the fresh template block, preserving
    # everything before BEGIN and after END. Read the block from the template
    # (first file) so multi-line content needs no shell-quoting gymnastics.
    tmp=$(mktemp "${SNAP_DIR}/agents.new.XXXXXX")
    awk -v b="$BEGIN_MARKER" -v e="$END_MARKER" '
      FNR==NR { block = block $0 "\n"; next }
      index($0, b) { if (!done) { printf "%s", block; done=1 } skip=1; next }
      index($0, e) { skip=0; next }
      !skip { print }
    ' "$AGENTS_TEMPLATE" "$AGENTS_TARGET" > "$tmp"
    mv "$tmp" "$AGENTS_TARGET"
    agents_action="replaced"
  else
    # Exists without the block — append it, guaranteeing a blank-line separator.
    [[ -n "$(tail -c1 "$AGENTS_TARGET")" ]] && printf '\n' >> "$AGENTS_TARGET"
    printf '\n' >> "$AGENTS_TARGET"
    cat "$AGENTS_TEMPLATE" >> "$AGENTS_TARGET"
    agents_action="appended"
  fi

  # --- .github/copilot-instructions.md: write wholesale ------------------
  mkdir -p "$(dirname "$COPILOT_TARGET")"
  local copilot_action="created"
  (( copilot_existed == 1 )) && copilot_action="overwritten"
  cp "$COPILOT_TEMPLATE" "$COPILOT_TARGET"

  # --- report ------------------------------------------------------------
  # Detect a no-op by comparing each target against its pre-write snapshot
  # (POSIX cmp, no external hash tool). A file that did not exist before is a
  # genuine create/overwrite, never "unchanged".
  if (( agents_existed == 1 )) && cmp -s "$AGENTS_TARGET" "${SNAP_DIR}/agents"; then
    agents_action="unchanged"
  fi
  if (( copilot_existed == 1 )) && cmp -s "$COPILOT_TARGET" "${SNAP_DIR}/copilot"; then
    copilot_action="unchanged"
  fi

  local state="scaffolded"
  [[ "$agents_action" == "unchanged" && "$copilot_action" == "unchanged" ]] && state="no-op"

  local override_json="false"
  (( OVERRIDE_MODE == 1 )) && override_json="true"

  trap - ERR
  rm -rf "$SNAP_DIR"

  jq -n \
    --arg state "$state" \
    --argjson override "$override_json" \
    --arg agents "$agents_action" \
    --arg copilot "$copilot_action" \
    '{state: $state, override: $override, agents_action: $agents, copilot_action: $copilot}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
