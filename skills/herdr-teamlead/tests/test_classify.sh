#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/classify/.
#
# The model is stubbed on PATH, never called: a classifier's output is
# non-deterministic, and calling one live would put that into the suite
# (rules/testing-standards.md Determinism, which names this case). What is under
# test is everything around the call -- the answer contract, the failure paths,
# and the scoring.
#
# The harness drops `set -e` to aggregate results; every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. A conforming answer      -> verdict, report hash, question hash, model.
#   2. A failed model call      -> exit 2, no stdout. Never a verdict.
#   3. An off-enum answer       -> exit 2. The schema is not advisory.
#   4. An empty answer          -> exit 2.
#   5. An unreadable report     -> exit 2 before any model call.
#   6. --model                  -> overrides the pin and travels with the label.
#   7. --out                    -> the same payload, byte for byte.
#   8. Corpus building          -> recorded verdicts only, missing files dropped.
#   9. Scoring                  -> accuracy, confusion, disagreements.
#  10. A failed classification  -> exit 1 with a partial score, never averaged
#                                 over the ones that worked.
#  11. Claude and Grok adapters -> each vendor's envelope unwrapped to the same
#                                 label; an errored or multi-answer run refused.
#  12. The empty room           -> every adapter runs where it can read nothing
#                                 but the question.

set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../classify" && pwd)" || die "cannot resolve the classify dir"

# A `codex` that writes whatever verdict the fixture asks for, into the file the
# real one would write, and ignores everything else.
stub_codex() { # <bin-dir> <exit> <answer-json>
  mkdir -p "$1" || die "mkdir $1"
  cat > "$1/codex" <<STUB || die "write codex stub"
#!/bin/sh
out=""
while [ \$# -gt 0 ]; do
  case "\$1" in --output-last-message) out="\$2"; shift 2 ;; *) shift ;; esac
done
cat > /dev/null
[ -n "\$out" ] && printf '%s' '$3' > "\$out"
exit $2
STUB
  chmod +x "$1/codex" || die "chmod codex stub"
}

classify() { # <bin-dir> <report> [extra args...]
  local bin="$1" report="$2"; shift 2
  OUT="$(PATH="$bin:$PATH" bash "$DIR/classify-report.sh" "$report" "$@" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

field() { printf '%s' "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$2"; }

main() {
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/classify-tests.XXXXXX")" || die "mktemp"
  trap 'rm -rf "$TMP"' EXIT
  ERRFILE="$TMP/err"

  local report="$TMP/report.md"
  printf '# Reviewer report\n\nB1: the parser accepts a quoted completion marker.\n' > "$report" \
    || die "write report fixture"

  echo "▶ the answer contract" >&2

  stub_codex "$TMP/ok" 0 '{"verdict":"blocking","evidence":"B1: the parser accepts a quoted completion marker."}'
  classify "$TMP/ok" "$report"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "blocking" ]] \
     && [[ "$(field "$OUT" model)" == "gpt-5.6-sol" ]]; then
    pass; else fail "a conforming answer returns its verdict and the pinned model, got RC=$RC OUT=$OUT"; fi

  # The label carries what makes a later prompt edit or model bump attributable.
  local expected_report expected_question
  expected_report="$(shasum -a 256 "$report" | cut -d' ' -f1)"
  expected_question="$(shasum -a 256 "$DIR/report-verdict.prompt.md" | cut -d' ' -f1)"
  if [[ "$(field "$OUT" sha256)" == "$expected_report" ]] \
     && [[ "$(field "$OUT" question)" == "$expected_question" ]]; then
    pass; else fail "the label must carry the report and question hashes, got OUT=$OUT"; fi

  classify "$TMP/ok" "$report" --model "some-other-model"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" model)" == "some-other-model" ]]; then
    pass; else fail "--model overrides the pin and travels with the label, got RC=$RC OUT=$OUT"; fi

  classify "$TMP/ok" "$report" --out "$TMP/label.json"
  if [[ $RC -eq 0 ]] && [[ "$(cat "$TMP/label.json")" == "$OUT" ]]; then
    pass; else fail "--out writes the same payload, got RC=$RC"; fi

  echo "▶ a failed call is never a verdict" >&2

  stub_codex "$TMP/down" 1 ''
  classify "$TMP/down" "$report"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'never a verdict'; then
    pass; else fail "a failed model call exits 2 with no verdict, got RC=$RC OUT=$OUT"; fi

  stub_codex "$TMP/offenum" 0 '{"verdict":"probably fine","evidence":"x"}'
  classify "$TMP/offenum" "$report"
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "an answer outside the enum exits 2, got RC=$RC OUT=$OUT"; fi

  stub_codex "$TMP/empty" 0 ''
  classify "$TMP/empty" "$report"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'no schema-conforming answer'; then
    pass; else fail "an empty answer exits 2, got RC=$RC OUT=$OUT"; fi

  classify "$TMP/ok" "$TMP/absent.md"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'not a readable file'; then
    pass; else fail "an unreadable report exits 2 before any call, got RC=$RC OUT=$OUT"; fi

  stub_codex "$TMP/abstain" 0 '{"verdict":"insufficient_evidence","evidence":""}'
  classify "$TMP/abstain" "$report"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "insufficient_evidence" ]]; then
    pass; else fail "an honest abstention is an answer, not a failure, got RC=$RC OUT=$OUT"; fi

  echo "▶ one adapter per kind" >&2

  # Claude wraps the answer in a stream of events; the last `result` holds it.
  mkdir -p "$TMP/cl" || die "mkdir cl"
  cat > "$TMP/cl/claude" <<'STUB' || die "write claude stub"
#!/bin/sh
cat > /dev/null
ls -A > "$ROOM_PROBE"
printf '%s' '[{"type":"system"},{"type":"result","is_error":false,"structured_output":{"verdict":"approved","evidence":"No blocking findings."}}]'
STUB
  chmod +x "$TMP/cl/claude" || die "chmod claude stub"
  OUT="$(ROOM_PROBE="$TMP/cl-room" PATH="$TMP/cl:$PATH" bash "$DIR/classify-report.sh" "$report" --agent claude 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "approved" ]] \
     && [[ "$(field "$OUT" agent)" == "claude" ]] && [[ "$(field "$OUT" model)" == "claude-sonnet-5" ]]; then
    pass; else fail "claude's envelope unwraps to a label with its pinned model, got RC=$RC OUT=$OUT"; fi
  # The adapter ran somewhere it could read nothing but the question.
  if [[ -f "$TMP/cl-room" && ! -s "$TMP/cl-room" ]]; then
    pass; else fail "the claude adapter must run in an empty directory, saw: $(cat "$TMP/cl-room" 2>/dev/null)"; fi

  cat > "$TMP/cl/claude" <<'STUB' || die "write erroring claude stub"
#!/bin/sh
cat > /dev/null
printf '%s' '[{"type":"result","is_error":true,"result":"usage limit reached"}]'
STUB
  OUT="$(PATH="$TMP/cl:$PATH" bash "$DIR/classify-report.sh" "$report" --agent claude 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'never a verdict'; then
    pass; else fail "an errored claude run is never a verdict, got RC=$RC OUT=$OUT"; fi

  # Grok puts the answer in `text`, and one turn gives exactly one object.
  mkdir -p "$TMP/gk" || die "mkdir gk"
  cat > "$TMP/gk/grok" <<'STUB' || die "write grok stub"
#!/bin/sh
ls -A > "$ROOM_PROBE"
printf '%s' '{"text":"{\"verdict\":\"blocking\",\"evidence\":\"B1\"}","stopReason":"end_turn"}'
STUB
  chmod +x "$TMP/gk/grok" || die "chmod grok stub"
  OUT="$(ROOM_PROBE="$TMP/gk-room" PATH="$TMP/gk:$PATH" bash "$DIR/classify-report.sh" "$report" --agent grok 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "blocking" ]] && [[ "$(field "$OUT" agent)" == "grok" ]]; then
    pass; else fail "grok's envelope unwraps to a label, got RC=$RC OUT=$OUT"; fi
  if [[ -f "$TMP/gk-room" && ! -s "$TMP/gk-room" ]]; then
    pass; else fail "the grok adapter must run in an empty directory, saw: $(cat "$TMP/gk-room" 2>/dev/null)"; fi

  # A live probe before these adapters existed: free to roam, grok searched the
  # workspace and emitted four concatenated answers. Picking one out is not the
  # answer to the question asked.
  cat > "$TMP/gk/grok" <<'STUB' || die "write chatty grok stub"
#!/bin/sh
printf '%s' '{"text":"{\"verdict\":\"insufficient_evidence\",\"evidence\":\"a\"}{\"verdict\":\"blocking\",\"evidence\":\"b\"}"}'
STUB
  OUT="$(PATH="$TMP/gk:$PATH" bash "$DIR/classify-report.sh" "$report" --agent grok 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'more than one answer'; then
    pass; else fail "more than one grok answer is refused, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  OUT="$(bash "$DIR/classify-report.sh" "$report" --agent gemini 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "an unknown agent is a usage error, got RC=$RC OUT=$OUT"; fi

  echo "▶ the labelled corpus" >&2

  local kept="$TMP/kept.md" state="$TMP/state.json"
  printf 'kept\n' > "$kept" || die "write kept report"
  python3 - "$state" "$kept" <<'PY' || die "write state fixture"
import json, sys
state, kept = sys.argv[1], sys.argv[2]
dispatches = [
    {"at": "2026-09-03T00:00:00Z", "role": "reviewer", "task": "t",
     "report": {"verdict": "blocking", "evidence": {"path": kept}}},
    {"at": "2026-09-02T00:00:00Z", "role": "tester", "task": "t",
     "report": {"verdict": "approved", "evidence": {"path": kept}}},
    {"at": "2026-09-01T00:00:00Z", "role": "reviewer", "task": "t",
     "report": {"verdict": "blocking", "evidence": {"path": "/nope/gone.md"}}},
    {"at": "2026-09-04T00:00:00Z", "role": "developer", "task": "t"},
]
with open(state, "w", encoding="utf-8") as handle:
    json.dump({"recovery": {"dispatches": dispatches}}, handle)
PY
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" 2>"$ERRFILE")"
  RC=$?
  # The same path twice is one report: a corpus that counted it twice would
  # score the same bytes twice. A missing file and a dispatch with no recorded
  # verdict are both dropped.
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" corpus)" == "1" ]]; then
    pass; else fail "the corpus keeps recorded verdicts with readable files only, got RC=$RC OUT=$OUT"; fi

  # --since keeps only reports recorded on or after the date, so a prompt change
  # can be measured on reports it was not written against.
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" --since 2026-09-03 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" corpus)" == "1" ]]; then
    pass; else fail "--since keeps a report recorded on the date, got RC=$RC OUT=$OUT"; fi
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" --since 2026-09-04 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'on or after 2026-09-04'; then
    pass; else fail "--since past every report names the date, got RC=$RC ERR=$ERRTEXT"; fi

  echo "▶ scoring" >&2

  stub_codex "$TMP/score" 0 '{"verdict":"blocking","evidence":"B1"}'
  OUT="$(PATH="$TMP/score:$PATH" bash "$DIR/evaluate.sh" --state "$state" 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" scored)" == "1" ]] \
     && [[ "$(field "$OUT" accuracy)" == "1.0" ]]; then
    pass; else fail "a correct prediction scores 1.0, got RC=$RC OUT=$OUT"; fi

  stub_codex "$TMP/wrong" 0 '{"verdict":"approved","evidence":"nothing blocks"}'
  OUT="$(PATH="$TMP/wrong:$PATH" bash "$DIR/evaluate.sh" --state "$state" 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" accuracy)" == "0.0" ]] \
     && printf '%s' "$OUT" | grep -q '"recorded": "blocking"' \
     && printf '%s' "$OUT" | grep -q '"predicted": "approved"'; then
    pass; else fail "a disagreement is reported with both sides, got RC=$RC OUT=$OUT"; fi

  stub_codex "$TMP/broken" 1 ''
  OUT="$(PATH="$TMP/broken:$PATH" bash "$DIR/evaluate.sh" --state "$state" 2>"$ERRFILE")"
  RC=$?
  # A partial score, never an accuracy averaged over only the calls that worked.
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" failed)" == "1" ]] \
     && [[ "$(field "$OUT" scored)" == "0" ]]; then
    pass; else fail "a failed classification exits 1 and is counted, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
