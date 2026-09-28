#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-foreman/classify/.
#
# The model is stubbed on PATH, never called: a classifier's output is
# non-deterministic, and calling one live would put that into the suite
# (rules/testing-standards.md Determinism, which names this case). Jev's HTTP
# layer is stubbed in tests/test_report_verdict.py; here TYPESAFE_API_KEY is
# unset, so every default run labels nothing and leaves the report for a full
# read. What is under test is everything around the call -- the answer
# contract, the unannotated path, the failure paths, and the scoring.
#
# The harness drops `set -e` to aggregate results; every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. A conforming answer      -> composed verdict, report hash, question hash, model.
#   2. A failed model call      -> exit 2, no stdout. Never a verdict.
#   3. An off-schema answer     -> exit 2. The schema is not advisory.
#   4. An empty answer          -> exit 2.
#   5. An unreadable report     -> exit 2 before any model call.
#   6. --model                  -> overrides the pin and travels with the label.
#   7. --out                    -> the same payload, byte for byte.
#   8. A fabricated quote       -> insufficient_evidence, never the model's verdict.
#   9. The default adapter      -> Jev unavailable labels nothing and asks no
#                                 other classifier; the report is read in full.
#  10. Claude and Grok adapters -> each vendor's envelope unwrapped to the same
#                                 label; an errored or multi-answer run refused.
#  11. The empty room           -> every adapter runs where it can read nothing
#                                 but the question.
#  12. A round's batch          -> one call annotates every report; a failed
#                                 annotation is reported, never fatal.
#  13. Corpus building          -> recorded verdicts only, missing files dropped;
#                                 held out by default; the default home is read
#                                 under the home guard.
#  14. Scoring                  -> accuracy, confusion, per-question accuracy,
#                                 disagreements, fixtures reported apart.
#  15. A failed classification  -> exit 1 with a partial score, never averaged
#                                 over the ones that worked.
#  16. A newline-named dir      -> every script still finds its siblings.

set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../classify" && pwd)" || die "cannot resolve the classify dir"

BLOCKING='{"names_open_item":{"answer":"yes","evidence":"B1: the parser accepts a quoted completion marker."},"open_items_accepted":{"answer":"no","evidence":""},"open_items_out_of_scope":{"answer":"no","evidence":""},"concludes_nothing_blocks":{"answer":"no","evidence":""}}'
APPROVED='{"names_open_item":{"answer":"no","evidence":""},"open_items_accepted":{"answer":"no","evidence":""},"open_items_out_of_scope":{"answer":"no","evidence":""},"concludes_nothing_blocks":{"answer":"yes","evidence":"No blocking findings."}}'
INVENTED='{"names_open_item":{"answer":"yes","evidence":"B7: the release step deletes the tag."},"open_items_accepted":{"answer":"no","evidence":""},"open_items_out_of_scope":{"answer":"no","evidence":""},"concludes_nothing_blocks":{"answer":"no","evidence":""}}'

# A `codex` that writes whatever answer the fixture asks for, into the file the
# real one would write, and ignores everything else.
stub_codex() { # <bin-dir> <exit> <answer-json>
  mkdir -p "$1" || die "mkdir $1"
  cat > "$1/codex" <<STUB || die "write codex stub"
#!/usr/bin/env bash
set -euo pipefail
out=""
while [ \$# -gt 0 ]; do
  case "\$1" in --output-last-message) out="\$2"; shift 2 ;; *) shift ;; esac
done
cat > /dev/null
if [ -n "\$out" ]; then printf '%s' '$3' > "\$out"; fi
exit $2
STUB
  chmod +x "$1/codex" || die "chmod codex stub"
}

# A `claude` that answers with the given object, and records what it could see.
stub_claude() { # <bin-dir> <answer-json>
  mkdir -p "$1" || die "mkdir $1"
  cat > "$1/claude" <<STUB || die "write claude stub"
#!/usr/bin/env bash
set -euo pipefail
cat > /dev/null
if [ -n "\${ROOM_PROBE:-}" ]; then ls -A > "\$ROOM_PROBE"; fi
printf '%s' '[{"type":"system"},{"type":"result","is_error":false,"structured_output":$2}]'
STUB
  chmod +x "$1/claude" || die "chmod claude stub"
}

classify() { # <bin-dir> <report> [extra args...]
  local bin="$1" report="$2"; shift 2
  OUT="$(PATH="$bin:$PATH" bash "$DIR/classify-report.sh" "$report" "$@" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

field() { printf '%s' "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$2"; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [ -n "${TMP:-}" ]; then rm -rf "$TMP"; fi; return 0; }

main() {
  # The suite never reaches TypeSafe, whatever the runner's environment holds.
  unset TYPESAFE_API_KEY
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/classify-tests.XXXXXX")" || die "mktemp"
  trap cleanup EXIT
  ERRFILE="$TMP/err"

  local report="$TMP/report.md" second="$TMP/second.md"
  printf '# Reviewer report\n\nB1: the parser accepts a quoted completion marker.\n' > "$report" \
    || die "write report fixture"
  printf '# Tester report\n\nNo blocking findings.\n' > "$second" || die "write second report"

  echo "▶ the answer contract" >&2

  stub_codex "$TMP/ok" 0 "$BLOCKING"
  classify "$TMP/ok" "$report" --agent codex
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "blocking" ]] \
     && [[ "$(field "$OUT" model)" == "gpt-5.6-sol" ]] \
     && [[ "$(field "$OUT" evidence)" == "B1: the parser accepts a quoted completion marker." ]]; then
    pass; else fail "a conforming answer composes its verdict with the pinned model, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # The label carries what makes a later question edit or model bump attributable.
  local expected_report expected_question
  expected_report="$(shasum -a 256 "$report" | cut -d' ' -f1)"
  expected_question="$(cat "$DIR/report-verdict.prompt.md" <(printf '\0') "$DIR/report-questions.json" | shasum -a 256 | cut -d' ' -f1)"
  if [[ "$(field "$OUT" sha256)" == "$expected_report" ]] \
     && [[ "$(field "$OUT" question)" == "$expected_question" ]]; then
    pass; else fail "the label must carry the report and question hashes, got OUT=$OUT"; fi

  classify "$TMP/ok" "$report" --agent codex --model "some-other-model"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" model)" == "some-other-model" ]]; then
    pass; else fail "--model overrides the pin and travels with the label, got RC=$RC OUT=$OUT"; fi

  classify "$TMP/ok" "$report" --model "some-other-model"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'no Jev label'; then
    pass; else fail "--model without --agent names a Jev model and asks nothing else, got RC=$RC OUT=$OUT"; fi

  classify "$TMP/ok" "$report" --agent codex --out "$TMP/label.json"
  if [[ $RC -eq 0 ]] && [[ "$(cat "$TMP/label.json")" == "$OUT" ]]; then
    pass; else fail "--out writes the same payload, got RC=$RC"; fi

  echo "▶ deterministic checks before semantic ones" >&2

  # A quote that is not in the report voids the verdict it claims to back.
  stub_codex "$TMP/invented" 0 "$INVENTED"
  classify "$TMP/invented" "$report" --agent codex
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "insufficient_evidence" ]] \
     && printf '%s' "$OUT" | grep -q '"evidence_verbatim": false'; then
    pass; else fail "a fabricated quote fails closed to insufficient_evidence, got RC=$RC OUT=$OUT"; fi

  # A report rewritten while the model reads it: the label hashes and checks
  # the bytes the model was given, never the rewrite.
  local moving="$TMP/moving.md" before
  cp "$report" "$moving" || die "copy moving report"
  before="$(shasum -a 256 "$moving" | cut -d' ' -f1)"
  mkdir -p "$TMP/rewrite" || die "mkdir rewrite"
  cat > "$TMP/rewrite/codex" <<STUB || die "write rewriting codex stub"
#!/usr/bin/env bash
set -euo pipefail
out=""
while [ \$# -gt 0 ]; do
  case "\$1" in --output-last-message) out="\$2"; shift 2 ;; *) shift ;; esac
done
cat > /dev/null
printf 'B1 is withdrawn.\n' > "$moving"
printf '%s' '$BLOCKING' > "\$out"
STUB
  chmod +x "$TMP/rewrite/codex" || die "chmod rewriting codex stub"
  classify "$TMP/rewrite" "$moving" --agent codex
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" sha256)" == "$before" ]] \
     && [[ "$(field "$OUT" verdict)" == "blocking" ]] && [[ "$(field "$OUT" report)" == "$moving" ]]; then
    pass; else fail "a mid-run rewrite never changes the bytes a label hashes, got RC=$RC OUT=$OUT"; fi

  echo "▶ a failed call is never a verdict" >&2

  stub_codex "$TMP/down" 1 ''
  classify "$TMP/down" "$report" --agent codex
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'never a verdict'; then
    pass; else fail "a failed model call exits 2 with no verdict, got RC=$RC OUT=$OUT"; fi

  # The whole schema is the contract: a missing question, an answer off the
  # enum, a missing evidence field or an extra field is no label at all.
  local shape rest='"open_items_accepted":{"answer":"no","evidence":""},"open_items_out_of_scope":{"answer":"no","evidence":""},"concludes_nothing_blocks":{"answer":"no","evidence":""}'
  for shape in '{"names_open_item":{"answer":"yes","evidence":"x"}}' \
               '{"names_open_item":{"answer":"probably","evidence":""},'"$rest"'}' \
               '{"names_open_item":{"answer":"no"},'"$rest"'}' \
               '{"names_open_item":{"answer":"no","evidence":""},'"$rest"',"verdict":"approved"}'; do
    stub_codex "$TMP/shape" 0 "$shape"
    classify "$TMP/shape" "$report" --agent codex
    if [[ $RC -eq 2 && -z "$OUT" ]]; then
      pass; else fail "answer '$shape' must be refused, got RC=$RC OUT=$OUT"; fi
  done

  stub_codex "$TMP/empty" 0 ''
  classify "$TMP/empty" "$report" --agent codex
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'no schema-conforming answer'; then
    pass; else fail "an empty answer exits 2, got RC=$RC OUT=$OUT"; fi

  classify "$TMP/ok" "$TMP/absent.md" --agent codex
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'not a readable file'; then
    pass; else fail "an unreadable report exits 2 before any call, got RC=$RC OUT=$OUT"; fi

  stub_codex "$TMP/abstain" 0 '{"names_open_item":{"answer":"unclear","evidence":""},'"$rest"'}'
  classify "$TMP/abstain" "$report" --agent codex
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "insufficient_evidence" ]]; then
    pass; else fail "an honest abstention is an answer, not a failure, got RC=$RC OUT=$OUT"; fi

  echo "▶ the default adapter asks no other classifier" >&2

  stub_claude "$TMP/cl" "$APPROVED"
  OUT="$(ROOM_PROBE="$TMP/cl-default-room" PATH="$TMP/cl:$PATH" bash "$DIR/classify-report.sh" "$second" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/cl-default-room" ]] \
     && printf '%s' "$ERRTEXT" | grep -q 'no Jev label (Jev unavailable: TYPESAFE_API_KEY is not set' \
     && printf '%s' "$ERRTEXT" | grep -q 'in full'; then
    pass; else fail "an unset key labels nothing and never calls claude, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  OUT="$(ROOM_PROBE="$TMP/cl-room" PATH="$TMP/cl:$PATH" bash "$DIR/classify-report.sh" "$second" --agent claude 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" agent)" == "claude" ]] && [[ "$(field "$OUT" model)" == "claude-sonnet-5" ]] \
     && printf '%s' "$OUT" | python3 -c 'import json,sys; assert json.load(sys.stdin)["gate"]["level"] is None'; then
    pass; else fail "an explicit claude label never gates, got RC=$RC OUT=$OUT"; fi
  # The adapter ran somewhere it could read nothing but the question.
  if [[ -f "$TMP/cl-room" && ! -s "$TMP/cl-room" ]]; then
    pass; else fail "the claude adapter must run in an empty directory, saw: $(cat "$TMP/cl-room")"; fi

  echo "▶ one adapter per kind" >&2

  cat > "$TMP/cl/claude" <<'STUB' || die "write erroring claude stub"
#!/usr/bin/env bash
set -euo pipefail
cat > /dev/null
printf '%s' '[{"type":"result","is_error":true,"result":"usage limit reached"}]'
STUB
  classify "$TMP/cl" "$report" --agent claude
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'never a verdict'; then
    pass; else fail "an errored claude run is never a verdict, got RC=$RC OUT=$OUT"; fi

  # Grok puts the answer in `text`, and one turn gives exactly one object.
  mkdir -p "$TMP/gk" || die "mkdir gk"
  python3 - "$TMP/gk/grok" "$BLOCKING" <<'PY' || die "write grok stub"
import json, sys
path, answer = sys.argv[1], sys.argv[2]
envelope = json.dumps({"text": answer, "stopReason": "end_turn"})
with open(path, "w", encoding="utf-8") as handle:
    handle.write("#!/usr/bin/env bash\nset -euo pipefail\nls -A > \"$ROOM_PROBE\"\nprintf '%s' '{}'\n".format(envelope))
PY
  chmod +x "$TMP/gk/grok" || die "chmod grok stub"
  OUT="$(ROOM_PROBE="$TMP/gk-room" PATH="$TMP/gk:$PATH" bash "$DIR/classify-report.sh" "$report" --agent grok 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "blocking" ]] && [[ "$(field "$OUT" agent)" == "grok" ]]; then
    pass; else fail "grok's envelope unwraps to a label, got RC=$RC OUT=$OUT"; fi
  if [[ -f "$TMP/gk-room" && ! -s "$TMP/gk-room" ]]; then
    pass; else fail "the grok adapter must run in an empty directory, saw: $(cat "$TMP/gk-room")"; fi

  # A live probe before these adapters existed: free to roam, grok searched the
  # workspace and emitted four concatenated answers. Picking one out is not the
  # answer to the question asked.
  cat > "$TMP/gk/grok" <<'STUB' || die "write chatty grok stub"
#!/usr/bin/env bash
set -euo pipefail
printf '%s' '{"text":"{\"a\":1}{\"b\":2}"}'
STUB
  classify "$TMP/gk" "$report" --agent grok
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'more than one answer'; then
    pass; else fail "more than one grok answer is refused, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  classify "$TMP/gk" "$report" --agent gemini
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "an unknown agent is a usage error, got RC=$RC OUT=$OUT"; fi

  echo "▶ a round's batch" >&2

  stub_claude "$TMP/batch" "$APPROVED"
  OUT="$(PATH="$TMP/batch:$PATH" bash "$DIR/classify-reports.sh" --agent claude "$second" "$second" 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["agent"] == "claude", d
assert len(d["labels"]) == 2 and d["unannotated"] == [], d
assert all(label["agent"] == "claude" for label in d["labels"]), d
'; then
    pass; else fail "one call annotates every report, got RC=$RC OUT=$OUT"; fi

  # The default batch with Jev unavailable: every report unannotated, read in full.
  OUT="$(ROOM_PROBE="$TMP/batch-room" PATH="$TMP/batch:$PATH" bash "$DIR/classify-reports.sh" "$second" 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 && ! -e "$TMP/batch-room" ]] && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["agent"] == "default" and d["labels"] == [], d
assert len(d["unannotated"]) == 1 and "TYPESAFE_API_KEY is not set" in d["unannotated"][0]["reason"], d
'; then
    pass; else fail "an unavailable Jev leaves the batch unannotated, got RC=$RC OUT=$OUT"; fi

  # An annotation that fails never blocks gating: the foreman reads that report as
  # it always has.
  OUT="$(PATH="$TMP/down:$PATH" bash "$DIR/classify-reports.sh" --agent codex "$report" "$second" 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["labels"] == [] and len(d["unannotated"]) == 2, d
assert all("never a verdict" in row["reason"] for row in d["unannotated"]), d
'; then
    pass; else fail "failed annotations are reported with their reason, never fatal, got RC=$RC OUT=$OUT"; fi

  OUT="$(bash "$DIR/classify-reports.sh" 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "no reports is a usage error, got RC=$RC OUT=$OUT"; fi

  echo "▶ the labelled corpus" >&2

  local kept="$TMP/kept.md" state="$TMP/state.json"
  cp "$report" "$kept" || die "write kept report"
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
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" --all 2>"$ERRFILE")"
  RC=$?
  # The same path twice is one report: a corpus that counted it twice would
  # score the same bytes twice. A missing file and a dispatch with no recorded
  # verdict are both dropped.
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" corpus)" == "1" ]] \
     && printf '%s' "$OUT" | grep -q '"held_out": false'; then
    pass; else fail "the corpus keeps recorded verdicts with readable files only, got RC=$RC OUT=$OUT"; fi

  # Held out by default: reports recorded before the questions changed are the
  # ones they may have been written against.
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'on or after 2026-09-27'; then
    pass; else fail "the default split is held out from the questions' change date, got RC=$RC ERR=$ERRTEXT"; fi

  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" --since 2026-09-03 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" corpus)" == "1" ]] && printf '%s' "$ERRTEXT" | grep -q 'not held out'; then
    pass; else fail "--since before the change date scores and says it is not held out, got RC=$RC OUT=$OUT"; fi
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" --since 2026-09-04 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'on or after 2026-09-04'; then
    pass; else fail "--since past every report names the date, got RC=$RC ERR=$ERRTEXT"; fi
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" --since 2026-09-03 --all 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "--since with --all is a usage error, got RC=$RC OUT=$OUT"; fi
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$state" --all --fixtures 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" fixtures)" == "3" ]]; then
    pass; else fail "--fixtures adds the adversarial fixtures apart from the corpus, got RC=$RC OUT=$OUT"; fi
  # An unreadable state file is a usage error that names the recovery.
  printf '{not json' > "$TMP/broken-state.json" || die "write broken state fixture"
  OUT="$(bash "$DIR/evaluate.sh" --corpus-only --state "$TMP/broken-state.json" --all 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'readable UTF-8 JSON'; then
    pass; else fail "an unreadable state file names its recovery, got RC=$RC ERR=$ERRTEXT"; fi

  # The default corpus refuses a state home still at the legacy teamlead path
  # rather than reading the new path as empty; a migrated home (legacy path
  # linked to the new one) reads normally.
  local xdg="$TMP/xdg"
  mkdir -p "$xdg/teamlead"
  cp "$state" "$xdg/teamlead/state.json"
  OUT="$(XDG_STATE_HOME="$xdg" bash "$DIR/evaluate.sh" --corpus-only --all 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'migrate-home'; then
    pass; else fail "a legacy default home names migrate-home, got RC=$RC ERR=$ERRTEXT"; fi
  # A legacy path that is not a directory, or a link elsewhere, is refused
  # the same way the owner refuses it, never read past as an empty store.
  local other="$TMP/xdg-other" blocked="$TMP/xdg-blocked"
  mkdir -p "$other/elsewhere" "$other/foreman" "$blocked"
  ln -s "$other/elsewhere" "$other/teamlead"
  printf 'x' > "$blocked/teamlead"
  local root
  for root in "$other" "$blocked"; do
    OUT="$(XDG_STATE_HOME="$root" bash "$DIR/evaluate.sh" --corpus-only --all 2>"$ERRFILE")"
    RC=$?
    ERRTEXT="$(cat "$ERRFILE")"
    if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'migrate-home'; then
      pass; else fail "a split or blocked legacy home under $root is refused, got RC=$RC ERR=$ERRTEXT"; fi
  done
  mv "$xdg/teamlead" "$xdg/foreman"
  ln -s "$xdg/foreman" "$xdg/teamlead"
  OUT="$(XDG_STATE_HOME="$xdg" bash "$DIR/evaluate.sh" --corpus-only --all 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" corpus)" == "1" ]]; then
    pass; else fail "a migrated default home reads its corpus, got RC=$RC OUT=$OUT"; fi
  # A migrate-home in flight holds the home guard exclusively. The default
  # corpus is checked and read under that guard, so it is refused rather than
  # scoring a half-moved or empty store. The holder runs evaluate while it
  # holds the lock, so the overlap is deterministic, never a timing race.
  local held
  held="$(python3 - "$xdg" "$DIR/evaluate.sh" "$ERRFILE" <<'PY'
import fcntl, os, subprocess, sys
xdg, evaluate, errfile = sys.argv[1:4]
with open(os.path.join(xdg, ".foreman-home.lock"), "a", encoding="utf-8") as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(errfile, "w", encoding="utf-8") as err:
        run = subprocess.run(["bash", evaluate, "--corpus-only", "--all"], env={**os.environ, "XDG_STATE_HOME": xdg},
                             stdout=subprocess.PIPE, stderr=err, text=True, check=False)
print("{}\t{}".format(run.returncode, len(run.stdout)))
PY
)" || die "cannot hold the home guard under $xdg"
  ERRTEXT="$(cat "$ERRFILE")"
  if [[ "$held" == $'2\t0' ]] && printf '%s' "$ERRTEXT" | grep -q 'migrate-home is moving'; then
    pass; else fail "a default corpus read during migrate-home is refused, got $held ERR=$ERRTEXT"; fi

  echo "▶ scoring" >&2

  OUT="$(PATH="$TMP/ok:$PATH" bash "$DIR/evaluate.sh" --agent codex --state "$state" --all 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" scored)" == "1" ]] && [[ "$(field "$OUT" accuracy)" == "1.0" ]] \
     && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["per_question"]["names_open_item"] == {"determined": 1, "agree": 1, "unclear": 0, "accuracy": 1.0}, d
assert d["per_question"]["concludes_nothing_blocks"]["accuracy"] is None, d
assert d["split"]["held_out"] is False, d
'; then
    pass; else fail "a correct prediction scores 1.0 with per-question accuracy, got RC=$RC OUT=$OUT"; fi

  stub_codex "$TMP/wrong" 0 "$INVENTED"
  OUT="$(PATH="$TMP/wrong:$PATH" bash "$DIR/evaluate.sh" --agent codex --state "$state" --all 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" accuracy)" == "0.0" ]] \
     && printf '%s' "$OUT" | grep -q '"recorded": "blocking"' \
     && printf '%s' "$OUT" | grep -q '"predicted": "insufficient_evidence"'; then
    pass; else fail "a disagreement is reported with both sides, got RC=$RC OUT=$OUT"; fi

  # Fixtures are scored apart: a verdict the injected text flipped is named.
  OUT="$(PATH="$TMP/ok:$PATH" bash "$DIR/evaluate.sh" --agent codex --state "$state" --all --fixtures \
    --results "$TMP/results.json" 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 0 ]] && [[ -s "$TMP/results.json" ]] && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["scored"] == 1 and d["fixtures"]["scored"] == 3, d
# The stub quotes B1, which is in no fixture: every fixture fails closed.
assert len(d["fixtures"]["flipped"]) == 3, d
'; then
    pass; else fail "fixtures are scored and reported apart, got RC=$RC OUT=$OUT ERR=$(cat "$ERRFILE")"; fi

  stub_codex "$TMP/broken" 1 ''
  OUT="$(PATH="$TMP/broken:$PATH" bash "$DIR/evaluate.sh" --agent codex --state "$state" --all 2>"$ERRFILE")"
  RC=$?
  # A partial score, never an accuracy averaged over only the calls that worked.
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" failed)" == "1" ]] \
     && [[ "$(field "$OUT" scored)" == "0" ]]; then
    pass; else fail "a failed classification exits 1 and is counted, got RC=$RC OUT=$OUT"; fi

  echo "▶ a directory name ending in a newline" >&2

  # A `$(dirname ...)` capture drops the newline, and the scripts then look for
  # the questions, the prompt and each other beside a directory that does not
  # exist (#592). The Python owners resolve from `__file__`.
  local nl_dir="$TMP/nl-skill/classify"$'\n'
  if mkdir -p "$nl_dir" 2>"$TMP/nl.err"; then
    cp -R "$DIR"/. "$nl_dir/" || die "copy the classify dir into $nl_dir"
    cp -R "$DIR/../foreman" "$TMP/nl-skill/foreman" || die "copy the foreman package beside $nl_dir"
    OUT="$(PATH="$TMP/ok:$PATH" bash "$nl_dir/classify-report.sh" "$report" --agent codex 2>"$ERRFILE")"
    RC=$?
    if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" verdict)" == "blocking" ]]; then
      pass; else fail "classify-report.sh in a newline-named dir, got RC=$RC OUT=$OUT ERR=$(cat "$ERRFILE")"; fi
    OUT="$(PATH="$TMP/batch:$PATH" bash "$nl_dir/classify-reports.sh" --agent claude "$second" "$second" 2>"$ERRFILE")"
    RC=$?
    if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert len(d["labels"]) == 2 and d["unannotated"] == [], d
'; then
      pass; else fail "classify-reports.sh in a newline-named dir, got RC=$RC OUT=$OUT ERR=$(cat "$ERRFILE")"; fi
    OUT="$(bash "$nl_dir/evaluate.sh" --corpus-only --state "$state" --all 2>"$ERRFILE")"
    RC=$?
    if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" corpus)" == "1" ]]; then
      pass; else fail "evaluate.sh in a newline-named dir, got RC=$RC OUT=$OUT ERR=$(cat "$ERRFILE")"; fi
  else
    echo "  skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/nl.err"))" >&2
  fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
