#!/usr/bin/env bash
# Run the report classifier over the labelled corpus and score it.
#
# The labels are not invented for this: the foreman recorded a verdict against
# every delivered report at the time it gated the round, and those verdicts sit
# in the recovery store. Written by a different agent, on a different day, for a
# different purpose -- which is what makes them usable as ground truth for a
# classifier written afterwards.
#
# Only 7 of the first 90 carry a `## BLOCKED` heading. A grep would score about
# 8% on recall, which is the measurement that says this destination is a
# classifier and not a script.
#
# Held out by default: a question or model change is scored only on reports
# recorded on or after the date the questions last changed (`changed` in
# report-questions.json), which the change cannot have been written against.
#
# Usage: evaluate.sh [--agent jev|codex|claude|grok] [--limit N] [--model <id>]
#                    [--state FILE] [--since DATE | --all] [--fixtures]
#                    [--results FILE] [--corpus-only]
#
#   --agent        the adapter scored; always explicit, so no run mixes a
#                  fallback's labels into another adapter's score. Default claude.
#   --since DATE   score reports recorded on or after DATE (ISO). Defaults to
#                  the questions' `changed` date.
#   --all          score the whole corpus; the split is marked not held out.
#   --fixtures     also score the adversarial fixtures in fixtures/, reported
#                  apart from the corpus.
#   --results FILE keep every label with its recorded verdict, the input to
#                  `scoring.py calibrate`.
#   --corpus-only  build and print the labelled corpus, call no model, spend no
#                  quota. Use it to see what would be scored.
#   --limit N      score the N most recent reports instead of all of them.
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object, `scoring.py score`'s report --
#     {"schema_version": 2, "agent", "model", "split": {"since", "changed",
#      "held_out"}, "scored", "failed", "accuracy", "confusion",
#      "per_question": {<id>: {"determined", "agree", "unclear", "accuracy"}},
#      "fixtures": {"scored", "per_question", "flipped"}, "disagreements"}
#   `disagreements` is the useful half: a label the classifier and the foreman
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

# Command substitution strips every trailing newline, so the script directory
# never passes through one bare: parameter expansion derives it (#487), and a
# sentinel carries `pwd` across the strip (#466).
case "${BASH_SOURCE[0]}" in
  */*) HERE_SRC="${BASH_SOURCE[0]%/*}" ;;
  *) HERE_SRC=. ;;
esac
if ! HERE="$(cd -- "${HERE_SRC:-/}" && pwd && printf x)"; then
  echo "evaluate: cannot enter the script directory ${HERE_SRC:-/} — restore read and search access to the plugin directory, or reinstall the plugin, then re-run" >&2
  exit 2
fi
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

SCRATCH=""
cleanup() { if [ -n "$SCRATCH" ]; then rm -rf "$SCRATCH"; fi; return 0; }
trap cleanup EXIT

die() { echo "evaluate: $*" >&2; exit 2; }

corpus() { # <state-file-or-empty> <limit> <since-or-empty> <state-root> <skill-dir>
  # An empty state file reads the default home. That run checks the home and
  # reads the store, and checks every report path it names, under one shared
  # hold of the home guard
  # (skills/herdr-foreman/foreman/home.py `guard`, `require_current`), so a
  # migrate-home starting between the check and the read is refused instead of
  # leaving a half-moved or empty corpus. An explicit --state is never moved
  # and takes no guard.
  XDG_STATE_HOME="$4" PYTHONPATH="$5" python3 - "$1" "$2" "${3-}" <<'PY'
import json, pathlib, sys

explicit, limit, since = sys.argv[1], int(sys.argv[2]), sys.argv[3]


def read(state):
    try:
        return json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write("evaluate: cannot read {}: {}. Restore it as readable UTF-8 JSON (the foreman's state "
                         "file), or pass --state with a readable copy.\n".format(state, exc))
        raise SystemExit(2)


def build(data):
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
                     "role": dispatch.get("role"), "task": dispatch.get("task"), "source": "corpus"})
    rows.sort(key=lambda row: row["at"], reverse=True)
    return json.dumps(rows[:limit] if limit > 0 else rows)


if explicit:
    corpus = build(read(pathlib.Path(explicit).expanduser()))
else:
    from foreman import home
    from foreman.errors import ForemanError
    try:
        # Held through the report-file checks too: reports can live under the
        # home, so a migration after the read could still drop them.
        with home.guard(False):
            home.require_current({"state"})
            corpus = build(read(home.roots()["state"] / home.CURRENT / home.STATE_FILE))
    except ForemanError as exc:
        sys.stderr.write("evaluate: {} Or pass --state.\n".format(exc.message))
        raise SystemExit(2)
print(corpus)
PY
}

with_fixtures() { # <corpus.json> <fixtures-dir>
  python3 - "$1" "$2" <<'PY'
import json, pathlib, sys
selected, fixtures = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
rows = json.loads(selected.read_text(encoding="utf-8"))
expected = json.loads((fixtures / "expected.json").read_text(encoding="utf-8"))
for row in expected["fixtures"]:
    rows.append({"report": str(fixtures / row["report"]), "recorded": row["verdict"], "at": "",
                 "role": None, "task": None, "source": "fixture", "expected_answers": row["answers"]})
selected.write_text(json.dumps(rows), encoding="utf-8")
PY
}

main() {
  local state_root="${XDG_STATE_HOME:-${HOME}/.local/state}"
  # HERE is absolute, so its parent comes from parameter expansion too (#487).
  local skill_dir="${HERE%/*}"
  skill_dir="${skill_dir:-/}"
  local limit=0 since="" all=0 model="" agent="claude" state="" corpus_only=0 fixtures=0 keep=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --limit) limit="${2-}"; shift 2 || die "--limit needs a count" ;;
      --since) since="${2-}"; shift 2 || die "--since needs an ISO date" ;;
      --all) all=1; shift ;;
      --model) model="${2-}"; shift 2 || die "--model needs an id" ;;
      --agent) agent="${2-}"; shift 2 || die "--agent needs jev, codex, claude or grok" ;;
      --state) state="${2-}"; shift 2 || die "--state needs a file" ;;
      --fixtures) fixtures=1; shift ;;
      --results) keep="${2-}"; shift 2 || die "--results needs a file" ;;
      --corpus-only) corpus_only=1; shift ;;
      -h|--help) sed -n '2,50p' "${BASH_SOURCE[0]}"; exit 0 ;;
      *) die "unknown argument '$1' -- see --help" ;;
    esac
  done
  case "$limit" in ''|*[!0-9]*) die "--limit takes a non-negative integer" ;; esac
  case "$agent" in jev|codex|claude|grok) ;; *) die "--agent '${agent}' is not one of jev, codex, claude, grok" ;; esac
  [ "$all" -eq 0 ] || [ -z "$since" ] || die "--since and --all contradict each other; pass one"
  local changed
  changed="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["changed"])' "${HERE}/report-questions.json")" \
    || die "cannot read the questions' change date from ${HERE}/report-questions.json"
  if [ "$all" -eq 0 ] && [ -z "$since" ]; then since="$changed"; fi
  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/classify-eval.XXXXXX")" || die "cannot create a temporary directory"
  SCRATCH="$work"

  local split="${work}/split.json"
  python3 -c 'import json,sys; since, changed = sys.argv[2], sys.argv[3]
json.dump({"since": since or None, "changed": changed, "held_out": bool(since) and since >= changed}, open(sys.argv[1], "w"))' \
    "$split" "$since" "$changed" || die "cannot record the split in ${work}"
  if [ -z "$since" ] || [[ "$since" < "$changed" ]]; then
    echo "evaluate: not held out -- the questions changed on ${changed}, and reports before it may be what they were written against" >&2
  fi

  local selected="${work}/corpus.json"
  local source="${state:-the default home under ${state_root}}"
  corpus "$state" "$limit" "$since" "$state_root" "$skill_dir" > "$selected" \
    || die "cannot build the labelled corpus from ${source}; fix the cause reported above, or pass --state with a readable state file"
  if [ "$fixtures" -eq 1 ]; then
    with_fixtures "$selected" "${HERE}/fixtures" || die "cannot add the fixtures in ${HERE}/fixtures"
  fi
  local total
  total="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$selected")" \
    || die "cannot read the corpus it just built at ${selected}"
  if [ "$total" -eq 0 ]; then
    if [ -n "$since" ]; then
      die "no labelled report was recorded on or after ${since}; run more rounds, pass an earlier --since, or --all for a score that is not held out"
    fi
    die "the corpus is empty: no delivered report carries a recorded verdict"
  fi

  if [ "$corpus_only" -eq 1 ]; then
    python3 -c 'import json,sys,collections; rows=json.load(open(sys.argv[1])); split=json.load(open(sys.argv[2]))
print(json.dumps({"schema_version":2,"scored":0,"split":split,"corpus":sum(r["source"]=="corpus" for r in rows),"fixtures":sum(r["source"]=="fixture" for r in rows),"recorded":dict(collections.Counter(r["recorded"] for r in rows if r["source"]=="corpus")),"reports":rows}, sort_keys=True))' "$selected" "$split" \
      || die "cannot summarize the corpus at ${selected}"
    return 0
  fi

  echo "evaluate: scoring ${total} report(s) on ${agent}; this spends one model call each" >&2
  local results="${work}/results.json" failures=0 index=0 report
  printf '[]' > "$results" || die "cannot write to ${work}"
  while IFS= read -r index; do
    report="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[int(sys.argv[2])]["report"])' "$selected" "$index")" \
      || die "cannot read row ${index} of ${selected}"
    echo "  [$((index + 1))/${total}] ${report}" >&2
    local answer="${work}/answer-${index}.json"
    if bash "${HERE}/classify-report.sh" "$report" --agent "$agent" ${model:+--model "$model"} --out "$answer" >/dev/null 2>"${work}/err-${index}"; then
      if ! python3 - "$results" "$answer" "$selected" "$index" <<'PY'
import json, sys
results, answer, selected, index = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
with open(results, encoding="utf-8") as handle:
    rows = json.load(handle)
with open(answer, encoding="utf-8") as handle:
    label = json.load(handle)
with open(selected, encoding="utf-8") as handle:
    row = json.load(handle)[index]
label.update(recorded=row["recorded"], source=row["source"])
if "expected_answers" in row:
    label["expected_answers"] = row["expected_answers"]
rows.append(label)
with open(results, "w", encoding="utf-8") as handle:
    json.dump(rows, handle)
PY
      then die "cannot record the label for ${report} in ${results}"; fi
    else
      failures=$((failures + 1))
      cat "${work}/err-${index}" >&2
    fi
  done < <(seq 0 $((total - 1)))

  if [ -n "$keep" ]; then
    cp "$results" "$keep" || die "cannot keep the labels at ${keep}"
  fi
  python3 "${HERE}/scoring.py" score "$results" "$failures" "$agent" "${model:-pinned}" "$split" \
    || die "cannot assemble the accuracy report from ${results}"
  [ "$failures" -eq 0 ] || return 1
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
