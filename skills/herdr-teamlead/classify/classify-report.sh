#!/usr/bin/env bash
# Does this worker report state a finding the lead must resolve?
#
# The first bounded classification in this plugin, and the one place in the
# lead's round where the destination is right: the input is 21KB of prose whose
# meaning has to be read, and the answer is one of a fixed set
# (rules/script-delegation.md Bounded Classification).
#
# The evidence that it is not a script: across 90 recorded reports the lead
# marked 63 `blocking` and 27 `approved`, while only 7 carry an explicit
# `## BLOCKED` heading. The verdict is not recoverable by grepping. It is in the
# prose or it is nowhere.
#
# The answer set carries `insufficient_evidence`, and that value routes the
# question back to the reasoning round -- here, the lead reads the report as it
# always has. This never suppresses that read (#482): it annotates, so the lead
# can gate several reports in one turn instead of one turn each.
#
# One adapter per kind the fleet already runs. A classifier pinned to one vendor
# is useless exactly when that vendor's subscription is spent, which is the
# condition the fleet spends most of its time managing. All three constrain the
# answer to the schema -- `codex exec --output-schema`, `claude --json-schema`,
# `grok --json-schema` -- and every adapter's answer then passes the same enum
# check, which is the actual guarantee.
#
# Every adapter runs from an empty directory, with no tools, for one turn. The
# classifier judges the report's own text: an adapter left free to search the
# workspace read this repository's tests and answered from them.
#
# Usage: classify-report.sh <report-path> [--agent codex|claude|grok]
#                           [--model <id>] [--out <file>]
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object --
#     {"schema_version": 1, "report": "<path>", "sha256": "<of the report>",
#      "question": "<sha256 of the prompt>", "agent": "<kind>", "model": "<id>",
#      "verdict": "blocking"|"approved"|"insufficient_evidence",
#      "evidence": "<the deciding sentence, verbatim>"}
#   The report hash, question hash and model id travel with every label, so a
#   prompt edit or a model bump is attributable (rules/dependency-management.md
#   Freshness: a pinned model version is a pinned dependency no scanner tracks).
#   stderr: diagnostics.
#
# Exit 0 on a label, including `insufficient_evidence` -- an honest abstention
# is an answer. Exit 2 on a usage error, an unreadable report, or a model call
# that produced nothing conforming to the schema. A failed call never becomes a
# verdict.
#
# Calling this costs model quota. `evaluate.sh` runs it over the labelled corpus
# and reports accuracy; nothing calls it automatically, and no test calls it live
# (rules/testing-standards.md Determinism).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd && printf x)"
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

#: The pinned model per kind. A bump is a dependency bump: change it here, and
#: every label recorded afterwards carries the new id. These are classification
#: pins, not the fleet's frontier seats: reading one report for one verdict does
#: not need the most expensive model a vendor sells.
#: The default is the vendor measured adequate for this job: against 90
#: lead-labelled reports claude-sonnet-5 caught 63 of 63 real blockers, grok-4.6
#: caught 62, and codex was unmeasured (its subscription was exhausted).
#: Renewal: these pins come due with the capability table, on `INTERVAL` in
#: skills/herdr-teamlead/teamlead/capabilities.py (weekly). At each refresh,
#: compare every pin against the table's current rows; a bump lands only after
#: `evaluate.sh --since <last bump>` scores the new model on reports it has not
#: seen, and the CHANGELOG records both numbers.
DEFAULT_AGENT="claude"
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
  local report="" out="" agent="$DEFAULT_AGENT" model=""
  [ $# -gt 0 ] || die "usage: classify-report.sh <report-path> [--agent codex|claude|grok] [--model <id>] [--out <file>]"
  report="$1"; shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --agent) agent="${2-}"; shift 2 || die "--agent needs codex, claude or grok" ;;
      --model) model="${2-}"; shift 2 || die "--model needs an id" ;;
      --out) out="${2-}"; shift 2 || die "--out needs a file" ;;
      *) die "unknown argument '$1'" ;;
    esac
  done
  [ -f "$report" ] && [ -r "$report" ] || die "'${report}' is not a readable file"
  local pinned
  pinned="$(model_for "$agent")" || die "--agent '${agent}' is not one of codex, claude, grok"
  [ -n "$model" ] || model="$pinned"

  local schema="${HERE}/report-verdict.schema.json" prompt="${HERE}/report-verdict.prompt.md"
  [ -r "$schema" ] || die "missing answer schema at ${schema}"
  [ -r "$prompt" ] || die "missing question at ${prompt}"
  command -v "$agent" >/dev/null || die "${agent} is not on PATH"

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/classify-report.XXXXXX")" || die "cannot create a temporary directory"
  SCRATCH="$work"
  local answer="${work}/answer.json" log="${work}/run.log" room="${work}/room"
  mkdir "$room" || die "cannot create the empty working directory"

  if ! ( cd "$room" && { cat "$prompt"; printf '\n\n----- REPORT BEGINS -----\n'; cat "$report"; } \
           | "ask_${agent}" "$model" "$schema" "$answer" "$log" ); then
    cat "$log" >&2
    die "the ${agent} call failed; a failed call is never a verdict"
  fi
  [ -s "$answer" ] || { cat "$log" >&2; die "${agent} wrote no schema-conforming answer"; }

  local payload
  payload="$(python3 "${HERE}/extract-answer.py" label "$answer" "$report" "$prompt" "$agent" "$model")" \
    || die "the ${agent} answer did not conform to the schema"

  printf '%s\n' "$payload"
  if [ -n "$out" ]; then
    printf '%s\n' "$payload" > "$out" || die "cannot write the label to ${out}"
  fi
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
