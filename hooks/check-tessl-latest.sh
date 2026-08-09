#!/usr/bin/env bash
# Warn at session start when a tessl.json jbaruch/* dependency is not "latest".
#
# A SessionStart hook: it is the deterministic enforcement for the
# Runtime-Managed Manifest Carve-Out (rules/dependency-management.md). tessl.json
# is a runtime-managed manifest — tessl rewrites the resolved state into the
# gitignored .tessl/ — so jbaruch/*-owned deps use the floating "latest"
# specifier. This hook reads tessl.json each session and surfaces any jbaruch/*
# dep pinned to something other than "latest", so a disallowed specifier is
# caught without a per-consumer deploy-time check. Third-party pins
# (tessl-labs/*, tessl/npm-*) are out of scope — they pin normally.
#
# Design choices, shared with hooks/check-git-sync.sh:
#   - It DOES something (reads the manifest), it does not re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn tax.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#   - No state; the manifest is read live each session.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read.
#   stdout: on a fire, one JSON object {"additionalContext": "<notice>"}; else nothing.
#   exit  : always 0. Best-effort failures warn to stderr and no-op
#           (rules/error-handling.md Shell Error Handling).
#   env   : TESSL_LATEST_MANIFEST (test-only; path to the manifest, default tessl.json).
set -euo pipefail

warn() { printf 'check-tessl-latest: %s\n' "$1" >&2; }

main() {
  local manifest="${TESSL_LATEST_MANIFEST:-tessl.json}"

  # No tessl.json => not a tessl consumer; nothing to check. Silent no-op.
  [[ -f "$manifest" ]] || return 0

  command -v jq >/dev/null 2>&1 \
    || { warn "jq not found — install jq to enable the tessl-latest check"; return 0; }

  # Collect jbaruch/* deps whose version is not "latest". A parse failure is
  # surfaced, not swallowed as "all latest".
  local pinned
  if ! pinned="$(jq -r '
      [.dependencies // {} | to_entries[]
       | select((.key | startswith("jbaruch/")) and .value.version != "latest")
       | "\(.key)@\(.value.version)"] | join(", ")' "$manifest" 2>/dev/null)"; then
    warn "could not parse ${manifest} — check it is valid JSON; skipping the tessl-latest check"
    return 0
  fi

  [[ -n "$pinned" ]] || return 0

  local notice="tessl.json pins jbaruch/* dependencies that should float to \`latest\` (Runtime-Managed Manifest Carve-Out, rules/dependency-management.md): ${pinned}. Set them to \`\"version\": \"latest\"\` — the resolved state lives in the gitignored \`.tessl/\`, so a pin only re-introduces the auto-update churn."

  jq -n --arg c "$notice" '{additionalContext: $c}' \
    || warn "could not emit the tessl-latest notice as JSON — skipping"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
