#!/usr/bin/env bash
# Re-surface the response-clarity directives every Nth user prompt.
#
# Tessl generic UserPromptSubmit hook. Tessl installs it on every agent that
# has a prompt-submit event (Claude Code, Codex); agents without one (Cursor,
# Gemini) simply don't get it. Reads the consensus hook payload on stdin,
# maintains a per-session turn counter, and on a fire turn emits
# {"additionalContext": <reminder>} — which Tessl translates into each agent's
# native context-injection form (Claude `hookSpecificOutput.additionalContext`,
# others `systemMessage`). Every other turn it stays silent.
#
# Why: rules/response-clarity.md is alwaysApply, but an always-on rule buried
# in the context wall loses salience over a long session (issue #254). This
# re-injects a compact reminder near the turn to counter that decay.
#
# Contract:
#   stdin : consensus hook JSON. Reads `.session_id` (string). Missing or
#           unparsable => session "default" (counter shared, still functions).
#   stdout: on a fire turn, one JSON object {"additionalContext": "<text>"};
#           otherwise nothing.
#   exit  : ALWAYS 0. Never 2 — exit 2 would block the user's prompt. Best
#           effort: an internal problem warns on stderr and no-ops, never
#           blocks (rules/error-handling.md — best-effort work continues past a
#           failure with a stderr warning).
#   state : $RESURFACE_STATE_DIR (default ${TMPDIR:-/tmp}/coding-policy-resurface),
#           one JSON file per session. Schema: hooks/state-schema.md.
#   env   : RESURFACE_INTERVAL (default 5) — fire on turn 1, then every Nth.
#           RESURFACE_STATE_DIR — state location (overridden by tests).
set -euo pipefail

SCHEMA_VERSION=1
RESURFACE_INTERVAL="${RESURFACE_INTERVAL:-5}"
STATE_DIR="${RESURFACE_STATE_DIR:-${TMPDIR:-/tmp}/coding-policy-resurface}"

# The reminder injected on a fire turn.
REMINDER='response-clarity is in effect — lead with the action (command, edit, or answer; no preamble); number multi-step work, one action per step; cap lists at 5; restate progress across turns; report errors plainly (expected vs actual); end with one concrete next step; no recap, no closer.'

warn() { printf 'resurface-response-clarity: %s\n' "$1" >&2; }

# Echo the stored turn_count for a session file, or 0 if absent/unreadable/
# corrupt/version-mismatched. Only a record stamped with the accepted
# SCHEMA_VERSION is trusted; any other version is no usable prior state
# (hooks/state-schema.md Migration; rules/stateful-artifacts.md Migration Policy).
read_count() {
  local f="$1" c
  [[ -r "$f" ]] || { echo 0; return; }
  c="$(jq -r --argjson v "$SCHEMA_VERSION" \
    'if (.schema_version == $v) then (.turn_count // 0) else 0 end' \
    "$f" 2>/dev/null || echo 0)"
  [[ "$c" =~ ^[0-9]+$ ]] || c=0
  echo "$c"
}

emit() {
  jq -n --arg c "$REMINDER" '{additionalContext: $c}'
}

main() {
  local input session file count

  # Validate the interval before any arithmetic — a non-numeric value would
  # abort the hook under set -e/set -u and break the never-block contract.
  if ! [[ "$RESURFACE_INTERVAL" =~ ^[1-9][0-9]*$ ]]; then
    warn "RESURFACE_INTERVAL='${RESURFACE_INTERVAL}' is not a positive integer — using 5"
    RESURFACE_INTERVAL=5
  fi

  # jq maintains the JSON turn counter; without it the hook can't track cadence
  # and would fire every turn, so degrade to a clean no-op instead.
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found — response-clarity re-surfacing disabled this session"
    exit 0
  fi

  input="$(cat)" || input=""

  # Explicit fallback on parse failure — not blanket suppression.
  session="$(printf '%s' "$input" | jq -r '.session_id // "default"' 2>/dev/null || echo default)"
  [[ -n "$session" ]] || session="default"
  # Sanitize for use as a filename component.
  session="${session//[^A-Za-z0-9_-]/_}"

  if ! mkdir -p "$STATE_DIR"; then
    warn "cannot create state dir ${STATE_DIR} — skipping re-surface this turn"
    exit 0
  fi
  file="${STATE_DIR}/${session}.json"

  count="$(read_count "$file")"
  # Force base-10: a digit string like "08" would otherwise be read as octal
  # and abort under set -e.
  count=$((10#$count + 1))

  if ! printf '{"schema_version":%d,"session_id":"%s","turn_count":%d}\n' \
        "$SCHEMA_VERSION" "$session" "$count" > "$file"; then
    warn "cannot write state file ${file} — re-surface may repeat"
  fi

  # Fire on turn 1, then every RESURFACE_INTERVAL turns: (count-1) % N == 0.
  # Guarding N>0 keeps a misconfigured interval of 0 from a divide-by-zero.
  if (( RESURFACE_INTERVAL > 0 && (count - 1) % RESURFACE_INTERVAL == 0 )); then
    emit
  fi
  exit 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
