#!/usr/bin/env bash
# Warn at session start when installed Tessl plugins are behind the registry.
#
# A SessionStart hook: runs `tessl outdated --json`, and if anything is behind,
# injects a one-line notice via additionalContext so the agent/user sees it and
# can `tessl update`. Fleet repos silently drift to stale policy versions with
# nothing flagging it (observed: consumers running an old coding-policy while a
# newer one is published); this surfaces that the moment a session opens.
#
# Design choices, learned from the reverted resurface hook:
#   - It DOES something (checks registry state), it does not re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn fleet tax.
#   - Throttled: a registry check runs at most once per FRESHNESS_THROTTLE_HOURS
#     (default 24h) across sessions, so most session starts skip the network call.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#
# Contract:
#   stdin : consensus SessionStart JSON (unused beyond being drained).
#   stdout: on a fire, one JSON object {"additionalContext": "<notice>"}; else nothing.
#   exit  : always 0. Degrades to a silent no-op if tessl/jq are missing, the
#           check errors, or the state dir is unwritable.
#   state : $FRESHNESS_STATE_DIR/last-check (default ${TMPDIR:-/tmp}/coding-policy-freshness),
#           a single epoch-seconds throttle stamp.
#   env   : FRESHNESS_THROTTLE_HOURS (default 24), FRESHNESS_STATE_DIR (tests).
set -euo pipefail

THROTTLE_HOURS="${FRESHNESS_THROTTLE_HOURS:-24}"
STATE_DIR="${FRESHNESS_STATE_DIR:-${TMPDIR:-/tmp}/coding-policy-freshness}"

warn() { printf 'check-policy-freshness: %s\n' "$1" >&2; }

cat >/dev/null 2>&1 || true   # drain stdin; we don't need its fields

# jq and tessl are both required to produce a signal; without either, no-op.
command -v jq >/dev/null 2>&1 || exit 0
command -v tessl >/dev/null 2>&1 || exit 0

if ! [[ "$THROTTLE_HOURS" =~ ^[0-9]+$ ]]; then
  warn "FRESHNESS_THROTTLE_HOURS='${THROTTLE_HOURS}' is not an integer — using 24"
  THROTTLE_HOURS=24
fi

now="$(date +%s)"
stamp="${STATE_DIR}/last-check"

# Throttle: skip the registry call if we checked within the window.
if [[ -r "$stamp" ]]; then
  last=""
  read -r last < "$stamp" 2>/dev/null || last=""
  [[ "$last" =~ ^[0-9]+$ ]] || last=0
  if (( now - last < THROTTLE_HOURS * 3600 )); then
    exit 0
  fi
fi

# Record the check up front so a slow/failed registry call still throttles the
# next session rather than hammering the registry on every start.
if mkdir -p "$STATE_DIR" 2>/dev/null; then
  printf '%s\n' "$now" > "$stamp" 2>/dev/null || warn "cannot write throttle stamp ${stamp}"
fi

# Explicit fallback: a tessl/network failure is a no-op, not a broken session.
out="$(tessl outdated --json 2>/dev/null)" || exit 0

# Build a compact notice line per outdated plugin. Empty list => silent.
notice="$(printf '%s' "$out" | jq -r '
  (.outdated // [])
  | map("- \(.current.tile.workspaceName)/\(.current.tile.tileName) \(.current.tile.version) -> \(.update.version)")
  | if length == 0 then empty else
      "Plugin updates available (run `tessl update`):\n" + (. | join("\n"))
    end
' 2>/dev/null)" || exit 0

[[ -n "$notice" ]] || exit 0
jq -n --arg c "$notice" '{additionalContext: $c}'
exit 0
