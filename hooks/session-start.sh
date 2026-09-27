#!/usr/bin/env bash
# Run every SessionStart hook and deliver all of their statuses at once.
#
# The plugin declares this script as its only SessionStart hook: native for
# Claude Code and Codex, and portable (through `tessl hook run`) for every other
# agent. `tessl hook run` keeps only the LAST hook's output in a group, and hands
# a hook only HOME, PATH, TMPDIR and TESSL_*, so HERDR_ENV never reaches it. The
# native entry keeps the session's environment; the portable run exits at once
# for an agent in NATIVE_AGENTS, so each session runs this once. One entry that
# merges the statuses itself delivers them all.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read; each hook gets /dev/null.
#   stdout: natively, {"hookSpecificOutput": {"hookEventName": "SessionStart",
#           "additionalContext": "<statuses>"}}; under tessl (TESSL_AGENT set,
#           not a native agent), {"additionalContext": "<statuses>"}. Either
#           joins every
#           hook's additionalContext with a blank line, in HOOKS order. Nothing
#           when no hook reported. A hook that exits non-zero or prints
#           something other than one additionalContext object is reported as
#           its own "Session-start status — hook <name> ..." line naming the
#           absolute path to run, never dropped.
#   stderr: each hook's stderr, passed through, plus this script's warnings.
#   exit  : always 0 — a failed hook is reported, never a failed session start.
#   needs : python3 or jq to read and encode JSON (python3 first). The hooks run
#           either way; with neither, the statuses are lost and a warning says so.
#   env   : SESSION_START_HOOKS overrides the hook list (tests only).
set -euo pipefail

#: Run in this order. Each is a script in this directory emitting at most one
#: {"additionalContext": ...} object and exiting 0.
HOOKS=(check-git-sync check-tessl-latest check-acr-latest herdr-team-status check-leftover-worktrees)

warn() { printf 'session-start: %s\n' "$1" >&2; }


#: Agents whose native SessionStart entry runs this script with the session's
#: environment; the portable (tessl-wrapped) run defers to it for them.
NATIVE_AGENTS=(claude-code codex)

#: native (the agent runs this directly) or portable (under `tessl hook run`,
#: which sets TESSL_AGENT and strips the rest of the environment).
MODE=native

# Print the additionalContext string of one hook output. Exit 1 when the output
# is not one object carrying a string additionalContext (the hook's fault), and
# 2 when no parser could answer (python3 and jq both failed or are missing). A
# python3 exit other than its verdicts 0 and 3 hands the question to jq. The trailing "x" keeps a status's own
# trailing newlines through the command substitution.
context_of() { # <output>
  local out rc=0
  if command -v python3 >/dev/null; then
    out="$(python3 -c '
import json, sys
try:
    doc = json.loads(sys.argv[1])
except ValueError:
    sys.exit(3)
ctx = doc.get("additionalContext") if isinstance(doc, dict) else None
if not isinstance(ctx, str):
    sys.exit(3)
sys.stdout.write(ctx + "x")
' "$1")" || rc=$?
    case "$rc" in
      0) printf '%s' "${out%x}"; return 0 ;;
      3) return 1 ;;
    esac
  fi
  if command -v jq >/dev/null; then
    # Raw slurp plus `fromjson?`: an output that is not JSON yields no value
    # (exit 4 under -e), the expected non-result; any other non-zero exit is
    # jq itself failing.
    rc=0
    out="$(jq -e -R -s -j 'fromjson? | select(type == "object" and (.additionalContext | type) == "string") | .additionalContext + "x"' <<<"$1")" || rc=$?
    case "$rc" in
      0) printf '%s' "${out%x}"; return 0 ;;
      4) return 1 ;;
      *) return 2 ;;
    esac
  fi
  return 2
}

# Print the payload for this mode: the native SessionStart payload Claude Code
# and Codex read, or the consensus {"additionalContext"} tessl translates.
# python3 first, then jq; the payload is printed only once a tool produced it,
# so a failed attempt never leaves partial output. Exit 1 when both failed.
encode() { # <text>
  local out
  if command -v python3 >/dev/null && out="$(python3 -c '
import json, sys
mode, text = sys.argv[1], sys.argv[2]
doc = {"additionalContext": text} if mode == "portable" else {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
print(json.dumps(doc))
' "$MODE" "$1")"; then
    printf '%s\n' "$out"
    return 0
  fi
  if command -v jq >/dev/null && out="$(jq -n --arg m "$MODE" --arg c "$1" \
      'if $m == "portable" then {additionalContext: $c} else {hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}} end')"; then
    printf '%s\n' "$out"
    return 0
  fi
  return 1
}

main() {
  local here name out rc ctx joined="" agent
  if [[ -n "${TESSL_AGENT:-}" ]]; then
    for agent in "${NATIVE_AGENTS[@]}"; do
      [[ "$TESSL_AGENT" != "$agent" ]] || return 0
    done
    MODE=portable
  fi
  # Child hooks that would write read this: under tessl the session's
  # environment is gone, so they report instead of acting.
  export SESSION_START_MODE="$MODE"
  local -a hooks statuses=() names=() codes=() outputs=()
  # Command substitution strips every trailing newline, so the hooks directory
  # never passes through one bare: parameter expansion derives it, and a
  # sentinel carries `pwd` across the strip (#487).
  local src dir
  src="${BASH_SOURCE[0]}"
  case "$src" in
    */*) dir="${src%/*}" ;;
    *) dir=. ;;
  esac
  here="$(cd -- "${dir:-/}" && pwd && printf x)" || { warn "cannot enter the hooks directory ${dir:-/} — reinstall the plugin"; return 0; }
  here="${here%x}"
  here="${here%$'\n'}"
  read -r -a hooks <<<"${SESSION_START_HOOKS:-${HOOKS[*]}}"

  # Run every hook first, so a missing JSON tool never stops one from acting.
  for name in "${hooks[@]}"; do
    rc=0
    out="$(bash "${here}/${name}.sh" </dev/null)" || rc=$?
    names+=("$name"); codes+=("$rc"); outputs+=("$out")
  done

  if ! command -v python3 >/dev/null && ! command -v jq >/dev/null; then
    warn "neither python3 nor jq is on PATH — the hooks ran, but their statuses cannot be delivered; install one of them"
    return 0
  fi

  local i
  for i in "${!names[@]}"; do
    name="${names[$i]}"
    if [[ "${codes[$i]}" != 0 ]]; then
      statuses+=("Session-start status — hook ${name} exited ${codes[$i]}; run \`bash ${here}/${name}.sh\` from this repo to see why.")
      continue
    fi
    [[ -n "${outputs[$i]//[[:space:]]/}" ]] || continue
    rc=0
    ctx="$(context_of "${outputs[$i]}")" || rc=$?
    case "$rc" in
      0) ;;
      1)
        statuses+=("Session-start status — hook ${name} printed something other than one additionalContext object; run \`bash ${here}/${name}.sh\` from this repo to see it.")
        continue ;;
      *)
        statuses+=("Session-start status — session-start could not parse hook ${name}'s output: python3 and jq both failed. Install or repair one of them, then start a new session.")
        continue ;;
    esac
    statuses+=("$ctx")
  done

  (( ${#statuses[@]} > 0 )) || return 0
  for i in "${!statuses[@]}"; do
    (( i > 0 )) && joined+=$'\n\n'
    joined+="${statuses[$i]}"
  done
  if ! encode "$joined"; then
    warn "neither python3 nor jq could encode the merged status — this session gets none; run 'bash ${here}/session-start.sh' to see why"
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no session-start status this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
  fi
  exit 0
fi
