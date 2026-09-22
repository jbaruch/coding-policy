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
# Usage: classify-report.sh <report-path> [--model <id>] [--out <file>]
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object --
#     {"schema_version": 1, "report": "<path>", "sha256": "<of the report>",
#      "question": "<sha256 of the prompt>", "model": "<id>",
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

#: The pinned model. A bump is a dependency bump: change it here, and every
#: label recorded afterwards carries the new id.
MODEL="gpt-5.6-sol"

SCRATCH=""
cleanup() { if [ -n "$SCRATCH" ]; then rm -rf "$SCRATCH"; fi; return 0; }
trap cleanup EXIT

die() { echo "classify-report: $*" >&2; exit 2; }

main() {
  local report="" out="" model="$MODEL"
  [ $# -gt 0 ] || die "usage: classify-report.sh <report-path> [--model <id>] [--out <file>]"
  report="$1"; shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --model) model="${2-}"; shift 2 || die "--model needs an id" ;;
      --out) out="${2-}"; shift 2 || die "--out needs a file" ;;
      *) die "unknown argument '$1'" ;;
    esac
  done
  [ -f "$report" ] && [ -r "$report" ] || die "'${report}' is not a readable file"

  local schema="${HERE}/report-verdict.schema.json" prompt="${HERE}/report-verdict.prompt.md"
  [ -r "$schema" ] || die "missing answer schema at ${schema}"
  [ -r "$prompt" ] || die "missing question at ${prompt}"

  command -v codex >/dev/null || die "codex is not on PATH; the classifier calls it with --output-schema"

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/classify-report.XXXXXX")" || die "cannot create a temporary directory"
  SCRATCH="$work"

  local answer="${work}/answer.json"
  if ! { cat "$prompt"; printf '\n\n----- REPORT BEGINS -----\n'; cat "$report"; } \
      | codex exec --json --skip-git-repo-check --sandbox read-only \
          --model "$model" --output-schema "$schema" --output-last-message "$answer" - \
          >"${work}/run.log" 2>&1; then
    cat "${work}/run.log" >&2
    die "the model call failed; a failed call is never a verdict"
  fi
  [ -s "$answer" ] || { cat "${work}/run.log" >&2; die "the model wrote no schema-conforming answer"; }

  local payload
  payload="$(python3 - "$answer" "$report" "$prompt" "$model" <<'PY'
import hashlib, json, sys
answer, report, prompt, model = sys.argv[1:5]
with open(answer, encoding="utf-8") as handle:
    label = json.load(handle)
if label.get("verdict") not in {"blocking", "approved", "insufficient_evidence"}:
    sys.stderr.write("classify-report: the answer is outside the schema's enum\n")
    raise SystemExit(2)

def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()

print(json.dumps({"schema_version": 1, "report": report, "sha256": digest(report),
                  "question": digest(prompt), "model": model,
                  "verdict": label["verdict"], "evidence": label.get("evidence", "")},
                 sort_keys=True))
PY
  )" || die "the model's answer did not conform to the schema"

  printf '%s\n' "$payload"
  if [ -n "$out" ]; then
    printf '%s\n' "$payload" > "$out" || die "cannot write the label to ${out}"
  fi
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
