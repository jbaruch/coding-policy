#!/usr/bin/env bash
# Ask one worker for its standup, as a plain message.
#
# The prompt is fixed text, the reply shape is fixed, and the report path is
# the worker's only channel back — so the whole thing is one deterministic
# send (`rules/script-delegation.md`). What the foreman does with the answers is
# the reasoning part, and that stays in the skill.
#
# The prompt goes out as a MESSAGE, never a slash command: a standup question
# is prose, and every slash-delivery quirk this fleet has hit (Grok reading a
# pasted `/usage` as chat, Codex swallowing the Enter behind its autocomplete)
# belongs to commands, not messages.
#
# Contract:
#   argv  : <agent-name> <report-path>
#           report-path must be absolute; the worker writes its four lines there.
#   stdout: one JSON object on exits 0, 3 and 4 —
#           {"agent":"<n>","report_path":"<p>","state":"<s>","sent":true}
#           exit 4 adds "pane_width" and "needed" (columns) with sent false.
#   stderr: diagnostics only.
#   exit  : 0 the prompt was accepted by a worker that was idle or done,
#           1 precondition unmet (usage, relative, over-long or control-character
#             path, not inside Herdr,
#             `herdr`, `jq` or the sibling foreman.sh absent),
#           2 a herdr failure, an unreadable `agent get` payload, or a failed
#             or unreadable `foreman marker-fit` measurement,
#           3 the worker is not idle or done — nothing was sent. A standup
#             never interrupts a turn (`rules/agent-team-operation.md`
#             Dispatch Safety),
#           4 the worker's live pane is too narrow for its `REPORT: <path>`
#             line — nothing was sent. A wrapped marker is one the wait can
#             never confirm.
#   env   : HERDR_BIN overrides the herdr binary; the tests point it at a fake.
#           It reaches `foreman marker-fit` as --herdr-bin, and PY_BIN passes
#           through to the foreman launcher. marker-fit reads no foreman home.
#           STANDUP_REPORT_PATH_MAX_COLS overrides the report path length
#           limit; a non-integer or zero value is a precondition failure.
set -euo pipefail

HERDR_BIN="${HERDR_BIN:-herdr}"

# States that may receive a message. Anything else is left alone.
READY_STATES="idle done"
# Longest report path the prompt may name; the same coarse bound
# compose-briefs.sh applies to a brief's REPORT. It rejects hopeless paths
# before Herdr is touched and never proves fit: the worker's live pane width
# decides that, measured by `foreman marker-fit` against the fit rule in
# skills/herdr-foreman/foreman/report_delivery.py (`marker_columns`).
STANDUP_REPORT_PATH_MAX_COLS="${STANDUP_REPORT_PATH_MAX_COLS:-100}"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOREMAN="${SKILL_DIR}/../herdr-foreman/foreman.sh"

ERRFILE=""
AGENT=""
REPORT_PATH=""

warn() { printf 'standup-ask: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

# The standup question. One message, four lines back, each capped so the table
# stays readable and a worker cannot answer with an essay.
standup_prompt() { # <report-path>
  printf '%s' "\
Daily standup. Answer with EXACTLY four lines, nothing before or after, and \
write the same four lines to ${1} (create the parent directory if needed):

DONE: <what you finished since the last standup, at most 25 words>
PLAN: <what you are doing next, at most 20 words>
BLOCKED: <what is blocking you, or the single word none>
REPORT: ${1}

No preamble, no markdown, no bullet points. If you have done nothing since the \
last standup, say so in DONE. Do not start any new work: answer, write the \
file, and stop."
}

# True when the text holds a character that breaks a one-row marker: a
# Unicode Cc control (C0 and DEL through the C locale's [[:cntrl:]], C1
# U+0080..U+009F by its UTF-8 bytes C2 80..9F) or the line and paragraph
# separators U+2028/U+2029 (E2 80 A8/A9). Bytewise under LC_ALL=C, so the
# answer never depends on the caller's locale. Mirrors `cmd_marker_fit`.
has_control() { # <text>
  local LC_ALL=C
  [[ "$1" == *[[:cntrl:]]* || "$1" == *$'\xc2'[$'\x80'-$'\x9f']* \
     || "$1" == *$'\xe2\x80'[$'\xa8'$'\xa9']* ]]
}

main() {
  if (( $# != 2 )); then
    warn "usage: standup-ask.sh <agent-name> <report-path>"
    return 1
  fi
  AGENT="$1"
  REPORT_PATH="$2"

  if [[ "$REPORT_PATH" != /* ]]; then
    warn "report path '${REPORT_PATH}' is relative — pass an absolute path; the worker resolves it in its own working directory, not yours"
    return 1
  fi
  if has_control "$REPORT_PATH"; then
    warn "report path contains a control character — the worker's \`REPORT: <path>\` line must be one printable row; pass a plain absolute path"
    return 1
  fi
  case "$STANDUP_REPORT_PATH_MAX_COLS" in
    ''|*[!0-9]*)
      warn "STANDUP_REPORT_PATH_MAX_COLS must be a positive integer, got '${STANDUP_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
      return 1
      ;;
  esac
  if (( 10#$STANDUP_REPORT_PATH_MAX_COLS < 1 )); then
    warn "STANDUP_REPORT_PATH_MAX_COLS must be a positive integer, got '${STANDUP_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
    return 1
  fi
  # Normalize to decimal once, so a validated `08` is not reparsed as octal.
  STANDUP_REPORT_PATH_MAX_COLS=$(( 10#$STANDUP_REPORT_PATH_MAX_COLS ))
  if (( ${#REPORT_PATH} > STANDUP_REPORT_PATH_MAX_COLS )); then
    warn "report path is ${#REPORT_PATH} characters; the limit is ${STANDUP_REPORT_PATH_MAX_COLS}, a coarse bound on the worker's \`REPORT: <path>\` line (the live pane width is checked before sending) — use a shorter reports directory (e.g. one under \$HOME/.local/state)"
    return 1
  fi
  if [[ "${HERDR_ENV:-}" != "1" ]]; then
    warn "not running inside Herdr (HERDR_ENV='${HERDR_ENV:-}') — run the standup from a pane Herdr manages"
    return 1
  fi
  local dep
  for dep in "$HERDR_BIN" jq; do
    if ! command -v "$dep" >/dev/null 2>&1; then
      warn "'${dep}' not found on PATH — install it to run the standup"
      return 1
    fi
  done
  if [[ ! -f "$FOREMAN" || ! -r "$FOREMAN" ]]; then
    warn "foreman.sh not found at ${FOREMAN} — herdr-standup runs beside the herdr-foreman skill; install both with \`tessl install jbaruch/coding-policy\`"
    return 1
  fi

  ERRFILE="$(mktemp)"
  trap cleanup EXIT

  local raw rc=0 state
  raw="$("$HERDR_BIN" agent get "$AGENT" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent get ${AGENT}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`${HERDR_BIN} agent list\` to see the live names"
    return 2
  fi
  rc=0
  state="$(printf '%s' "$raw" | jq -r '
    if (.result.agent | type) != "object" then
      error("herdr agent get payload has no .result.agent object")
    else
      .result.agent.agent_status // "unknown"
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the state from the herdr agent get payload (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi

  # A standup is worth less than somebody's turn. A worker that is not ready
  # keeps working, and the foreman fills its row from the round log instead.
  if [[ " $READY_STATES " != *" $state "* ]]; then
    warn "${AGENT} is '${state}' — not asking. Fill its row from the round log."
    jq -n --arg a "$AGENT" --arg p "$REPORT_PATH" --arg s "$state" \
      '{agent: $a, report_path: $p, state: $s, sent: false}'
    return 3
  fi

  # The live pane decides whether the worker's marker stays on one row; the
  # fit rule lives in the foreman package, never restated here.
  local fit verdict width needed fits
  rc=0
  fit="$(bash "$FOREMAN" marker-fit --herdr-bin "$HERDR_BIN" --agent "$AGENT" --report "$REPORT_PATH" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`foreman marker-fit\` for ${AGENT} failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — nothing was sent"
    return 2
  fi
  rc=0
  verdict="$(printf '%s' "$fit" | jq -r '
    if (.fits | type) != "boolean" or (.pane_width | type) != "number" or (.needed | type) != "number" then
      error("marker-fit payload lacks fits, pane_width or needed")
    else
      "\(.fits) \(.pane_width) \(.needed)"
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the \`foreman marker-fit\` verdict (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — nothing was sent"
    return 2
  fi
  read -r fits width needed <<<"$verdict"
  if [[ "$fits" != "true" ]]; then
    warn "${AGENT}'s pane is ${width} columns; its \`REPORT: <path>\` line needs ${needed}, so it would wrap and the wait could never confirm it. Nothing was sent — widen the pane or use a shorter reports directory, then ask again."
    jq -n --arg a "$AGENT" --arg p "$REPORT_PATH" --arg s "$state" \
      --argjson w "$width" --argjson n "$needed" \
      '{agent: $a, report_path: $p, state: $s, sent: false, pane_width: $w, needed: $n}'
    return 4
  fi

  rc=0
  "$HERDR_BIN" agent prompt "$AGENT" "$(standup_prompt "$REPORT_PATH")" >/dev/null 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent prompt ${AGENT}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi

  jq -n --arg a "$AGENT" --arg p "$REPORT_PATH" --arg s "$state" \
    '{agent: $a, report_path: $p, state: $s, sent: true}'
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
