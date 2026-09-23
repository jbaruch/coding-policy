#!/usr/bin/env bash
# Run the report classifier over the labelled corpus and score it.
#
# The labels are not invented for this: the lead recorded a verdict against
# every delivered report at the time it gated the round, and those verdicts sit
# in the recovery store. 90 reports, 63 `blocking`, 27 `approved`. Written by a
# different agent, on a different day, for a different purpose -- which is what
# makes them usable as ground truth for a classifier written afterwards.
#
# Only 7 of the 90 carry a `## BLOCKED` heading. A grep would score about 8% on
# recall, which is the measurement that says this destination is a classifier
# and not a script.
#
# Usage: evaluate.sh [--agent codex|claude|grok] [--limit N] [--model <id>]
#                    [--state FILE] [--corpus-only]
#
#   --corpus-only  build and print the labelled corpus, call no model, spend no
#                  quota. Use it to see what would be scored.
#   --limit N      score the N most recent reports instead of all of them.
#   --since DATE   score only reports recorded on or after DATE (ISO). Use it to
#                  measure a prompt change on reports it was not written against.
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object --
#     {"schema_version": 1, "agent": "<kind>", "model": "<id>", "scored": N,
#      "accuracy": <float>, "confusion": {"<recorded>__<predicted>": N},
#      "disagreements": [{"report", "recorded", "predicted", "evidence"}, ...]}
#   `disagreements` is the useful half: a label the classifier and the lead
#   disagree on is either a classifier error or a report whose verdict was
#   never legible from its own text, and only reading it says which.
#   stderr: per-report progress.
#
# Exit 0 when every selected report was scored. Exit 1 when any classification
# failed -- a partial score is reported, never silently averaged over fewer.
# Exit 2 on a usage or tool error.
#
# THIS SPENDS MODEL QUOTA: one call per report. `--corpus-only` spends none.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd && printf x)"
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

SCRATCH=""
cleanup() { if [ -n "$SCRATCH" ]; then rm -rf "$SCRATCH"; fi; return 0; }
trap cleanup EXIT

die() { echo "evaluate: $*" >&2; exit 2; }

corpus() { # <state-file> <limit> <since-or-empty>
  python3 - "$1" "$2" "${3-}" <<'PY'
import json, pathlib, sys
state, limit, since = pathlib.Path(sys.argv[1]).expanduser(), int(sys.argv[2]), sys.argv[3]
try:
    data = json.loads(state.read_text(encoding="utf-8"))
except (OSError, ValueError) as exc:
    sys.stderr.write("evaluate: cannot read {}: {}\n".format(state, exc))
    raise SystemExit(2)
rows, seen = [], set()
for dispatch in data.get("recovery", {}).get("dispatches", []):
    report = dispatch.get("report") or {}
    evidence = report.get("evidence")
    verdict = report.get("verdict")
    if not (isinstance(evidence, dict) and evidence.get("path")) or verdict not in {"blocking", "approved"}:
        continue
    path = pathlib.Path(evidence["path"])
    # ISO timestamps order as strings, so a date prefix selects everything
    # recorded on or after it.
    if since and dispatch.get("at", "") < since:
        continue
    if path in seen or not path.is_file():
        continue
    seen.add(path)
    rows.append({"report": str(path), "recorded": verdict, "at": dispatch.get("at", ""),
                 "role": dispatch.get("role"), "task": dispatch.get("task")})
rows.sort(key=lambda row: row["at"], reverse=True)
print(json.dumps(rows[:limit] if limit > 0 else rows))
PY
}

main() {
  local limit=0 since="" model="" agent="claude" state="${HOME}/.local/state/teamlead/state.json" corpus_only=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --limit) limit="${2-}"; shift 2 || die "--limit needs a count" ;;
      --since) since="${2-}"; shift 2 || die "--since needs an ISO date" ;;
      --model) model="${2-}"; shift 2 || die "--model needs an id" ;;
      --agent) agent="${2-}"; shift 2 || die "--agent needs codex, claude or grok" ;;
      --state) state="${2-}"; shift 2 || die "--state needs a file" ;;
      --corpus-only) corpus_only=1; shift ;;
      -h|--help) sed -n '2,33p' "${BASH_SOURCE[0]}"; exit 0 ;;
      *) die "unknown argument '$1' -- see --help" ;;
    esac
  done
  case "$limit" in ''|*[!0-9]*) die "--limit takes a non-negative integer" ;; esac

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/classify-eval.XXXXXX")" || die "cannot create a temporary directory"
  SCRATCH="$work"

  local selected="${work}/corpus.json"
  corpus "$state" "$limit" "$since" > "$selected" || die "cannot build the labelled corpus"
  local total
  total="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$selected")" \
    || die "cannot read the corpus it just built at ${selected}"
  if [ "$total" -eq 0 ]; then
    if [ -n "$since" ]; then
      die "no labelled report was recorded on or after ${since}; run more rounds, or pass an earlier date"
    fi
    die "the corpus is empty: no delivered report carries a recorded verdict"
  fi

  if [ "$corpus_only" -eq 1 ]; then
    python3 -c 'import json,sys; rows=json.load(open(sys.argv[1])); import collections; print(json.dumps({"schema_version":1,"scored":0,"corpus":len(rows),"recorded":dict(collections.Counter(r["recorded"] for r in rows)),"reports":rows}, sort_keys=True))' "$selected" \
      || die "cannot summarize the corpus at ${selected}"
    return 0
  fi

  echo "evaluate: scoring ${total} report(s) on ${agent}; this spends one model call each" >&2
  local results="${work}/results.json" failures=0 index=0 report recorded answer
  printf '[]' > "$results" || die "cannot write to ${work}"
  local queue="${work}/queue.tsv"
  python3 -c 'import json,sys
for row in json.load(open(sys.argv[1])):
    print(row["report"] + "\t" + row["recorded"])' "$selected" > "$queue" \
    || die "cannot list the corpus at ${selected}"
  while IFS=$'\t' read -r report recorded; do
    index=$((index + 1))
    echo "  [${index}/${total}] ${report}" >&2
    answer="${work}/answer-${index}.json"
    if bash "${HERE}/classify-report.sh" "$report" --agent "$agent" ${model:+--model "$model"} --out "$answer" >/dev/null 2>"${work}/err-${index}"; then
      if ! python3 - "$results" "$answer" "$recorded" <<'PY'
import json, sys
results, answer, recorded = sys.argv[1:4]
with open(results, encoding="utf-8") as handle:
    rows = json.load(handle)
with open(answer, encoding="utf-8") as handle:
    label = json.load(handle)
rows.append({**label, "recorded": recorded})
with open(results, "w", encoding="utf-8") as handle:
    json.dump(rows, handle)
PY
      then die "cannot record the label for ${report} in ${results}"; fi
    else
      failures=$((failures + 1))
      cat "${work}/err-${index}" >&2
    fi
  done < "$queue"

  if ! python3 - "$results" "$failures" "${model:-pinned}" "$agent" <<'PY'
import collections, json, sys
results, failures, model, agent = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
with open(results, encoding="utf-8") as handle:
    rows = json.load(handle)
confusion = collections.Counter("{}__{}".format(r["recorded"], r["verdict"]) for r in rows)
agree = sum(n for key, n in confusion.items() if key.split("__")[0] == key.split("__")[1])
print(json.dumps({"schema_version": 1, "agent": agent, "model": rows[0]["model"] if rows else model,
                  "scored": len(rows), "failed": failures,
                  "accuracy": round(agree / len(rows), 4) if rows else None,
                  "confusion": dict(confusion),
                  "disagreements": [{"report": r["report"], "recorded": r["recorded"],
                                     "predicted": r["verdict"], "evidence": r.get("evidence", "")}
                                    for r in rows if r["recorded"] != r["verdict"]]},
                 sort_keys=True))
PY
  then die "cannot assemble the accuracy report from ${results}"; fi
  [ "$failures" -eq 0 ] || return 1
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
