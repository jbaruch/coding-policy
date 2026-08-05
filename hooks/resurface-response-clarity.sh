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

# The reminder injected on a fire turn. Must stay free of " and \ so the
# jq-absent fallback in emit() can hand-build valid JSON.
REMINDER='response-clarity is in effect — lead with the action (command, edit, or answer; no preamble); number multi-step work, one action per step; cap lists at 5; restate progress across turns; report errors plainly (expected vs actual); end with one concrete next step; no recap, no closer.'

warn() { printf 'resurface-response-clarity: %s\n' "$1" >&2; }

# Echo the stored turn_count for a session file, or 0 if absent/unreadable/corrupt.
read_count() {
  local f="$1" c
  [[ -r "$f" ]] || { echo 0; return; }
  if command -v jq >/dev/null 2>&1; then
    c="$(jq -r '.turn_count // 0' "$f" 2>/dev/null || echo 0)"
  else
    c=0
  fi
  [[ "$c" =~ ^[0-9]+$ ]] || c=0
  echo "$c"
}

emit() {
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg c "$REMINDER" '{additionalContext: $c}'
  else
    # jq absent: REMINDER is guaranteed free of " and \, so this is valid JSON.
    printf '{"additionalContext":"%s"}\n' "$REMINDER"
  fi
}

main() {
  local input session file count

  input="$(cat)" || input=""

  session="default"
  if command -v jq >/dev/null 2>&1; then
    # Explicit fallback on parse failure — not blanket suppression.
    session="$(printf '%s' "$input" | jq -r '.session_id // "default"' 2>/dev/null || echo default)"
  fi
  [[ -n "$session" ]] || session="default"
  # Sanitize for use as a filename component.
  session="${session//[^A-Za-z0-9_-]/_}"

  if ! mkdir -p "$STATE_DIR"; then
    warn "cannot create state dir ${STATE_DIR} — skipping re-surface this turn"
    exit 0
  fi
  file="${STATE_DIR}/${session}.json"

  count="$(read_count "$file")"
  count=$((count + 1))

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
