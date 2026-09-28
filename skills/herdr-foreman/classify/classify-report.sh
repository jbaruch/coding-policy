#!/usr/bin/env bash
# Does this worker report state a finding the foreman must resolve?
#
# The first bounded classification in this plugin, and the one place in the
# foreman's round where the destination is right: the input is 21KB of prose whose
# meaning has to be read, and the answer is one of a fixed set
# (rules/script-delegation.md Bounded Classification).
#
# The evidence that it is not a script: across 90 recorded reports the foreman
# marked 63 `blocking` and 27 `approved`, while only 7 carry an explicit
# `## BLOCKED` heading. The verdict is not recoverable by grepping. It is in the
# prose or it is nowhere.
#
# The answer set carries `insufficient_evidence`, and that value routes the
# question back to the reasoning round -- here, the foreman reads the report as it
# always has. A label never suppresses that read and never approves anything:
# the only effect one can have is a gate that adds friction
# (foreman/report_gates.py).
#
# The question is split into atomic questions (report-questions.json) and the
# verdict is composed from their answers in code, so no model weighs the policy
# that combines them. Deterministic checks run first: the report is framed as
# untrusted data, and every quote an LLM returns must be a passage of the
# report or the label is `insufficient_evidence` (report_verdict.py).
#
# Adapters, in the order the default chain tries them:
#   jev    -- TypeSafe System One, one Noul per atomic question, P(yes) each.
#             Needs TYPESAFE_API_KEY. The only adapter whose label can gate.
#   claude -- the fallback. With no --agent, a Jev that is unavailable (key
#             unset, service down, report too large, answer off contract)
#             falls back here, says so on stderr, and records the reason in the
#             label's `fallback`. A fallback label never gates.
#   codex, grok -- named with --agent only. A classifier pinned to one vendor
#             is useless exactly when that vendor's subscription is spent.
# Each LLM adapter constrains its answer to the generated schema --
# `codex exec --output-schema`, `claude --json-schema`, `grok --json-schema` --
# and every answer then passes the same check in report_verdict.py, which is
# the actual guarantee. Every LLM adapter runs from an empty directory, with no
# tools, for one turn: an adapter left free to search the workspace read this
# repository's tests and answered from them.
#
# Usage: classify-report.sh <report-path> [--agent jev|codex|claude|grok]
#                           [--model <id>] [--out <file>]
#   --model needs --agent: the default chain spans two vendors.
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one label, the JSON object report_verdict.py documents: verdict
#     `blocking`|`approved`|`insufficient_evidence`, the deciding passage in
#     `evidence` (empty for Jev, which quotes nothing), one answer per atomic
#     question with P(yes) for Jev, `fallback`, and the `gate` the owner would
#     record. The report hash, question hash and model id travel with every
#     label, so a question edit or a model bump is attributable
#     (rules/dependency-management.md Freshness).
#   stderr: diagnostics, and one line for every fallback.
#
# Exit 0 on a label, including `insufficient_evidence` -- an honest abstention
# is an answer. Exit 2 on a usage error, an unreadable report, a report that
# contains its own delimiter, or a call that produced nothing conforming. A
# failed call is never a verdict.
#
# Calling this costs model quota. `evaluate.sh` runs it over the labelled corpus
# and reports accuracy; nothing calls it automatically, and no test calls it live
# (rules/testing-standards.md Determinism).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd && printf x)"
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

#: The pinned LLM model per kind. A bump is a dependency bump: change it here,
#: and every label recorded afterwards carries the new id. These are
#: classification pins, not the fleet's frontier seats. The Jev pin lives with
#: its bands in foreman/report_gates.py (`JEV_MODEL`): bands are per model
#: version.
#: The fallback is the vendor measured adequate for this job: against 90
#: lead-labelled reports claude-sonnet-5 caught 63 of 63 real blockers, grok-4.6
#: caught 62, and codex was unmeasured (its subscription was exhausted).
#: Renewal: these pins come due with the capability table, on `INTERVAL` in
#: skills/herdr-foreman/foreman/capabilities.py (weekly). At each refresh,
#: compare every pin against the table's current rows; a bump lands only after
#: `evaluate.sh --since <last bump>` scores the new model on reports it has not
#: seen, and the CHANGELOG records both numbers.
DEFAULT_AGENT="jev"
FALLBACK_AGENT="claude"
model_for() { # <kind>
  case "$1" in
    codex) echo "gpt-5.6-sol" ;;
    claude) echo "claude-sonnet-5" ;;
    grok) echo "grok-4.6" ;;
    *) return 1 ;;
  esac
}

SCRATCH=""
cleanup() { if [ -n "$SCRATCH" ]; then rm -rf "$SCRATCH"; fi; return 0; }
trap cleanup EXIT

die() { echo "classify-report: $*" >&2; exit 2; }

# Each adapter reads the whole question on stdin, writes the model's answer
# object to <answer>, and returns non-zero when the vendor call failed.
ask_codex() { # <model> <schema> <answer> <log>
  codex exec --json --skip-git-repo-check --sandbox read-only \
    --model "$1" --output-schema "$2" --output-last-message "$3" - >"$4" 2>&1
}

ask_claude() { # <model> <schema> <answer> <log>
  local raw="${3}.raw"
  claude -p --model "$1" --output-format json --json-schema "$(cat "$2")" \
    --tools "" --strict-mcp-config >"$raw" 2>"$4" || return 1
  python3 "${HERE}/extract-answer.py" claude "$raw" "$3" 2>>"$4"
}

ask_grok() { # <model> <schema> <answer> <log>
  local raw="${3}.raw" question
  question="$(cat)"
  grok -m "$1" --json-schema "$(cat "$2")" --max-turns 1 --no-subagents \
    --disable-web-search --tools "" -p "$question" </dev/null >"$raw" 2>"$4" || return 1
  python3 "${HERE}/extract-answer.py" grok "$raw" "$3" 2>>"$4"
}

main() {
  local report="" out="" agent="" model=""
  [ $# -gt 0 ] || die "usage: classify-report.sh <report-path> [--agent jev|codex|claude|grok] [--model <id>] [--out <file>]"
  report="$1"; shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --agent) agent="${2-}"; shift 2 || die "--agent needs jev, codex, claude or grok" ;;
      --model) model="${2-}"; shift 2 || die "--model needs an id" ;;
      --out) out="${2-}"; shift 2 || die "--out needs a file" ;;
      *) die "unknown argument '$1'" ;;
    esac
  done
  [ -f "$report" ] && [ -r "$report" ] || die "'${report}' is not a readable file"
  case "$agent" in
    ''|jev|codex|claude|grok) ;;
    *) die "--agent '${agent}' is not one of jev, codex, claude, grok" ;;
  esac
  if [ -n "$model" ] && [ -z "$agent" ]; then
    die "--model needs --agent: the default chain (${DEFAULT_AGENT}, then ${FALLBACK_AGENT}) spans two vendors"
  fi
  local verdict="${HERE}/report_verdict.py"
  [ -r "$verdict" ] || die "missing the question owner at ${verdict}"

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/classify-report.XXXXXX")" || die "cannot create a temporary directory"
  SCRATCH="$work"
  local answer="${work}/answer.json" log="${work}/run.log" room="${work}/room"
  local payload="" fallback="" rc=0

  if [ -z "$agent" ] || [ "$agent" = "jev" ]; then
    if payload="$(python3 "$verdict" jev "$report" ${model:+--model "$model"} 2>"$log")"; then
      :
    else
      rc=$?
      if [ "$rc" -eq 3 ] && [ -z "$agent" ]; then
        fallback="$(tail -n 1 "$log")"
        fallback="${fallback#report_verdict: }"
        echo "classify-report: ${fallback}; falling back to ${FALLBACK_AGENT}, whose label never gates" >&2
        agent="$FALLBACK_AGENT"
        payload=""
      else
        cat "$log" >&2
        die "the jev call failed; a failed call is never a verdict"
      fi
    fi
  fi

  if [ -z "$payload" ]; then
    local pinned
    pinned="$(model_for "$agent")" || die "no pinned model for '${agent}'"
    [ -n "$model" ] || model="$pinned"
    command -v "$agent" >/dev/null || die "${agent} is not on PATH"
    local schema="${work}/schema.json" question="${work}/question.txt"
    python3 "$verdict" schema "$schema" || die "cannot write the answer schema to ${schema}"
    python3 "$verdict" frame "$report" > "$question" || die "cannot frame ${report} as data; read it in full"
    mkdir "$room" || die "cannot create the empty working directory"
    if ! ( cd "$room" && "ask_${agent}" "$model" "$schema" "$answer" "$log" < "$question" ); then
      cat "$log" >&2
      die "the ${agent} call failed; a failed call is never a verdict"
    fi
    [ -s "$answer" ] || { cat "$log" >&2; die "${agent} wrote no schema-conforming answer"; }
    payload="$(python3 "$verdict" label "$answer" "$report" "$agent" "$model" ${fallback:+--fallback "$fallback"})" \
      || die "the ${agent} answer did not conform to the schema"
  fi

  printf '%s\n' "$payload"
  if [ -n "$out" ]; then
    printf '%s\n' "$payload" > "$out" || die "cannot write the label to ${out}"
  fi
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
