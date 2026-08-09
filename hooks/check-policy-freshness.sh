#!/usr/bin/env bash
# Warn at session start when installed Tessl plugins are behind the registry.
#
# A SessionStart hook: runs `tessl outdated --json`, and if anything is behind,
# injects a short notice via additionalContext so the agent/user sees it and can
# `tessl update`. Fleet repos silently drift to stale policy versions with
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
#   stdin : consensus SessionStart JSON — not read (the script needs none of it).
#   stdout: on a fire, one JSON object {"additionalContext": "<notice>"}; else nothing.
#           The notice may span multiple lines (a header plus one line per plugin).
#   exit  : always 0. Every best-effort failure emits an actionable stderr warning
#           and continues/no-ops (rules/error-handling.md Shell Error Handling).
#   state : $FRESHNESS_STATE_DIR/last-check (default ${TMPDIR:-/tmp}/coding-policy-freshness),
#           a single epoch-seconds throttle stamp.
#   env   : FRESHNESS_THROTTLE_HOURS (default 24), FRESHNESS_STATE_DIR (tests),
#           FRESHNESS_NOW (test-only injected clock; defaults to `date +%s`).
set -euo pipefail

THROTTLE_HOURS="${FRESHNESS_THROTTLE_HOURS:-24}"
STATE_DIR="${FRESHNESS_STATE_DIR:-${TMPDIR:-/tmp}/coding-policy-freshness}"

warn() { printf 'check-policy-freshness: %s\n' "$1" >&2; }

# jq and tessl are both required to produce a signal; a missing optional tool is
# an expected environment condition, not a failure — warn and no-op.
command -v jq >/dev/null 2>&1 || { warn "jq not found — install jq to enable the freshness check"; exit 0; }
command -v tessl >/dev/null 2>&1 || { warn "tessl not found — install the Tessl CLI to enable the freshness check"; exit 0; }

if ! [[ "$THROTTLE_HOURS" =~ ^[0-9]+$ ]]; then
  warn "FRESHNESS_THROTTLE_HOURS='${THROTTLE_HOURS}' is not an integer — using 24"
  THROTTLE_HOURS=24
fi

# Resolve the clock. A test may inject FRESHNESS_NOW; otherwise read the system
# clock and handle its failure. Validate the result as an integer before any
# arithmetic so a malformed value can't abort the hook under set -e.
if [[ -n "${FRESHNESS_NOW:-}" ]]; then
  now="$FRESHNESS_NOW"
elif ! now="$(date +%s)"; then
  warn "cannot read the system clock — skipping freshness check"
  exit 0
fi
if ! [[ "$now" =~ ^[0-9]+$ ]]; then
  warn "clock value '${now}' is not an integer — unset FRESHNESS_NOW; skipping freshness check"
  exit 0
fi

stamp="${STATE_DIR}/last-check"

# Throttle: skip the registry call if we checked within the window.
if [[ -r "$stamp" ]]; then
  last=""
  read -r last < "$stamp" || last=""
  [[ "$last" =~ ^[0-9]+$ ]] || last=0
  if (( now - last < THROTTLE_HOURS * 3600 )); then
    exit 0
  fi
fi

# Record the check up front so a slow/failed registry call still throttles the
# next session rather than hammering the registry on every start.
if mkdir -p "$STATE_DIR"; then
  printf '%s\n' "$now" > "$stamp" ||
    warn "cannot write throttle stamp ${stamp} — check permissions on ${STATE_DIR}; will re-check next session"
else
  warn "cannot create state dir ${STATE_DIR} — check permissions or set FRESHNESS_STATE_DIR; freshness check will not throttle"
fi

# Silence tessl's own diagnostic but explicitly handle the failure and warn —
# an offline/registry error is a no-op, not a broken session.
if ! out="$(tessl outdated --json 2>/dev/null)"; then
  warn "tessl outdated failed — check connectivity and retry \`tessl outdated\`; skipping freshness check"
  exit 0
fi

# Build a notice line per outdated plugin. Empty list => silent. A parse failure
# is surfaced, not swallowed as "nothing outdated".
if ! notice="$(printf '%s' "$out" | jq -r '
  (.outdated // [])
  | map("- \(.current.tile.workspaceName)/\(.current.tile.tileName) \(.current.tile.version) -> \(.update.version)")
  | if length == 0 then empty else
      "Plugin updates available (run `tessl update`):\n" + (. | join("\n"))
    end
' 2>/dev/null)"; then
  warn "could not parse \`tessl outdated --json\` output — run it manually to inspect; skipping freshness check"
  exit 0
fi

[[ -n "$notice" ]] || exit 0

if ! jq -n --arg c "$notice" '{additionalContext: $c}'; then
  warn "could not emit the update notice as JSON — skipping freshness check"
  exit 0
fi
exit 0
