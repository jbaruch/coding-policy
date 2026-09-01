#!/usr/bin/env bash
# Wait for one worker's round report to land, then report what was observed.
#
# Completion is TWO signals, never one: the report FILE exists on disk AND the
# worker's pane shows the `REPORT: ` marker line its brief ends with. Herdr's
# lifecycle state alone does not decide it — a Claude Code pane reports `done`
# between tool calls while the turn is still running, and a Grok pane reports
# `working` while idle at startup, so a single idle/done observation would end
# the wait on a worker that has produced nothing.
#
# Contract:
#   argv  : <agent-name> <report-path>
#           agent-name  a live Herdr agent name (or the pane id hosting it).
#           report-path absolute path the brief told that worker to write.
#   stdout: one JSON object, emitted on every terminal outcome —
#           {"agent":"<n>","state":"<s>","report_path":"<p>",
#            "found":<bool>,"elapsed_seconds":<int>}
#   stderr: diagnostics and per-attempt progress.
#   exit  : 0 report found (`found` true),
#           1 budget exhausted (`found` false, `state` last observed),
#           2 usage error, precondition unmet, or a herdr/tool failure,
#           3 the worker is blocked at an approval or question dialog
#             (`found` false) — inspect the dialog with
#             `herdr agent read <name> --source visible` before answering it.
#   env   : HERDR_ENV must be 1. HERDR_BIN overrides the herdr binary.
#           Poll interval, give-up budget, and the pane-probe parameters are
#           the named constants below (rules/ci-safety.md Always Watch CI —
#           poll interval and budget are script-owned, never agent-chosen);
#           each is env-overridable for tests and for a longer-running round.
set -euo pipefail

# Seconds between attempts. One attempt always runs before the budget check,
# so a zero budget still probes once.
TEAMLEAD_WAIT_INTERVAL_SEC="${TEAMLEAD_WAIT_INTERVAL_SEC:-15}"
# Give-up budget in seconds. A worker round on a real task runs long; this is
# the point at which the lead inspects the pane by hand instead of waiting.
TEAMLEAD_WAIT_BUDGET_SEC="${TEAMLEAD_WAIT_BUDGET_SEC:-5400}"
# Per-attempt pane-probe timeout in milliseconds. `herdr pane wait-output`
# searches the existing snapshot first, so this bounds one probe, not the wait.
TEAMLEAD_PROBE_TIMEOUT_MS="${TEAMLEAD_PROBE_TIMEOUT_MS:-2000}"
# Rows of the visible snapshot searched for the marker. Claude Code and Grok
# render on the alternate screen, so rows that scrolled off are unrecoverable;
# the marker is the LAST line of the final message and stays on screen.
TEAMLEAD_PROBE_LINES="${TEAMLEAD_PROBE_LINES:-40}"
# The literal the brief requires as the final message's last line.
REPORT_MARKER_REGEX='REPORT: '

HERDR_BIN="${HERDR_BIN:-herdr}"

ERRFILE=""

warn() { printf 'wait-report: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

emit() { # <state> <found-bool> <elapsed-seconds>
  jq -n --arg a "$AGENT" --arg s "$1" --arg p "$REPORT_PATH" \
        --argjson f "$2" --argjson e "$3" \
    '{agent: $a, state: $s, report_path: $p, found: $f, elapsed_seconds: $e}'
}

# Echo "<state> <pane_id>" for the agent, or return 2 on a herdr failure.
agent_info() { # <agent-name>
  local raw rc=0 parsed
  raw="$("$HERDR_BIN" agent get "$1" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent get $1\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`${HERDR_BIN} agent list\` to see the live names"
    return 2
  fi
  rc=0
  parsed="$(printf '%s' "$raw" | jq -r '
    if (.result.agent | type) != "object" then
      error("herdr agent get payload has no .result.agent object")
    else
      "\(.result.agent.agent_status // "unknown") \(.result.agent.pane_id // "unknown")"
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the herdr agent get payload (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi
  printf '%s\n' "$parsed"
  return 0
}

# 0 = marker on screen, 1 = not yet (the expected no-result), 2 = tool failure.
# `herdr pane wait-output` reports a no-match as exit 1 with an
# {"error":{"code":"timeout"}} payload on stderr; every other error code is a
# real failure and must not read as "the worker is still working"
# (rules/error-handling.md — distinguish an expected non-result from a fault).
marker_seen() { # <pane-id>
  local rc=0 code
  "$HERDR_BIN" pane wait-output "$1" \
    --regex "$REPORT_MARKER_REGEX" \
    --source visible \
    --lines "$TEAMLEAD_PROBE_LINES" \
    --timeout "$TEAMLEAD_PROBE_TIMEOUT_MS" >/dev/null 2>"$ERRFILE" || rc=$?
  if (( rc == 0 )); then return 0; fi
  code="$(jq -r '.error.code // "unparseable"' < "$ERRFILE" 2>/dev/null)" || code="unparseable"
  if [[ "$code" == "timeout" ]]; then return 1; fi
  warn "\`${HERDR_BIN} pane wait-output $1\` failed (exit ${rc}, code ${code}): $(tr '\n' ' ' < "$ERRFILE")"
  return 2
}

main() {
  if (( $# != 2 )); then
    warn "usage: wait-report.sh <agent-name> <report-path>"
    return 2
  fi
  AGENT="$1"
  REPORT_PATH="$2"

  if [[ "${HERDR_ENV:-}" != "1" ]]; then
    warn "not running inside Herdr (HERDR_ENV='${HERDR_ENV:-}') — run the team round from a pane Herdr manages"
    return 2
  fi
  if ! command -v "$HERDR_BIN" >/dev/null 2>&1; then
    warn "'${HERDR_BIN}' not found on PATH — install the herdr CLI (https://herdr.dev) or point HERDR_BIN at the binary"
    return 2
  fi
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to parse the herdr payload"
    return 2
  fi

  local start now elapsed info state pane rc marker
  if ! start="$(date +%s)"; then
    warn "cannot read the system clock — the wait cannot be bounded"
    return 2
  fi

  ERRFILE="$(mktemp)"
  trap cleanup EXIT

  state="unknown"
  while :; do
    rc=0
    info="$(agent_info "$AGENT")" || rc=$?
    if (( rc != 0 )); then return 2; fi
    state="${info%% *}"
    pane="${info##* }"

    # A blocked worker is waiting on a human, not producing a report. Return
    # now so the lead inspects the dialog instead of burning the budget.
    if [[ "$state" == "blocked" ]]; then
      now="$(date +%s)"
      emit "$state" false "$(( now - start ))"
      warn "${AGENT} is blocked at an approval or question dialog — inspect it with \`${HERDR_BIN} agent read ${AGENT} --source visible\`"
      return 3
    fi

    rc=0
    marker_seen "$pane" || rc=$?
    if (( rc == 2 )); then return 2; fi
    marker=$(( rc == 0 ? 1 : 0 ))

    if (( marker == 1 )) && [[ -f "$REPORT_PATH" ]]; then
      now="$(date +%s)"
      emit "$state" true "$(( now - start ))"
      return 0
    fi

    now="$(date +%s)"
    elapsed=$(( now - start ))
    if (( elapsed >= TEAMLEAD_WAIT_BUDGET_SEC )); then
      emit "$state" false "$elapsed"
      warn "${AGENT} produced no report within ${TEAMLEAD_WAIT_BUDGET_SEC}s (marker seen: ${marker}, file present: $([[ -f "$REPORT_PATH" ]] && echo 1 || echo 0)) — read the pane with \`${HERDR_BIN} agent read ${AGENT} --source visible\` before re-dispatching"
      return 1
    fi

    sleep "$TEAMLEAD_WAIT_INTERVAL_SEC"
  done
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
