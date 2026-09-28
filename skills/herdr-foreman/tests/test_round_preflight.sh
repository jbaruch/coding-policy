#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-foreman/round-preflight.sh.
#
# The preflight composes existing owner scripts, so each case stubs those
# scripts in a shadow plugin directory rather than standing up Herdr, a GitHub
# token and a git worktree tree. What is under test is the AGGREGATION: which
# exit codes block a round, which cadence surfaces as due, and whether one
# failing check hides another's result.
#
# The harness drops `set -e` to aggregate results, so every fixture command is
# checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Every check clean        -> ready, exit 0, nothing blocking.
#   2. HERDR_ENV unset          -> standalone, exit 1, no other check run.
#   3. Roster fails             -> blocks, names roster.sh and its code.
#   4. Authority fails          -> blocks; an unanswerable check is not permission.
#   5. Two checks fail          -> both reported; neither hides the other.
#   6. Capability due           -> surfaces in `due`, does NOT block.
#   7. Prune exit 1 vs 2        -> distinct statuses, both blocking; a sweep
#                                  past its budget (a stand-in runner's 124)
#                                  blocks too.
#   8. --no-measure             -> headroom skipped, foreman still verified, ready.
#      Measure fails            -> foreman tier a dependency failure, not verified.
#      Measure output unreadable-> headroom blocked, foreman tier not verified.
#      No foreman block + failed measure -> unconfigured warning, not a block.
#   9. Missing --repo/--checkout-> exit 2, usage error, no JSON verdict.
#  10. Every check's payload    -> carried through under `checks.<name>.detail`.
#  11. Gate pointers            -> resolved once, carried for the briefs.
#  12. Non-object payload       -> exit 0 with `[]`/`null` blocks, never `ok`.
#  13. Newline-named plugin dir -> the collaborators are still found.
#  14. Foreman tier unproven    -> blocks, names `foreman verify-foreman`.
#  15. Foreman unconfigured     -> warns on stderr, `unconfigured`, still ready.

set -uo pipefail

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ FAIL: $*" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }

REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || die "cannot resolve the plugin dir"

stub() { # <dir> <name> <exit> <stdout>
  printf '#!/usr/bin/env bash\nset -euo pipefail\nprintf %s\nexit %s\n' "'$4'" "$3" > "$1/$2" || die "write stub $2"
  chmod +x "$1/$2" || die "chmod stub $2"
}

# A shadow plugin dir: the real preflight beside stubbed collaborators, so the
# script under test is the only unstubbed thing in it.
shadow() { # <dir> [roster-rc] [authority-rc] [prune-rc] [capability-due] [authorized]
  local dir="$1" roster="${2:-0}" authority="${3:-0}" prune="${4:-0}" due="${5:-false}" authorized="${6:-true}"
  mkdir -p "$dir" || die "mkdir $dir"
  cp "$REAL/round-preflight.sh" "$dir/" || die "copy the script under test"
  cp "$REAL/foreman-tier-check.py" "$dir/" || die "copy the composite foreman-tier check"
  stub "$dir" roster.sh "$roster" '{"agents":[{"name":"grok"}]}'
  stub "$dir" verify-authority.sh "$authority" "{\"authorized\":${authorized}}"
  local sweep_out='{"repos":[],"skipped":[]}'
  case "$prune" in
    2) sweep_out='{"repos":[{"shared":"/tmp","exit":2}],"skipped":[]}' ;;
    other) prune=2; sweep_out='{"repos":[{"shared":"/elsewhere","exit":1}],"skipped":[],"errors":[{"path":"/w/x","repo":"/elsewhere","exit":128,"error":"e"}]}' ;;
    orphan) prune=2; sweep_out='{"repos":[],"skipped":[],"errors":[{"path":"/w/x","repo":null,"exit":128,"error":"e"}]}' ;;
    mine) prune=2; sweep_out='{"repos":[],"skipped":[],"errors":[{"path":"/w/x","repo":"/tmp","exit":128,"error":"e"}]}' ;;
    noresult) prune=2; sweep_out='{"repos":[{"shared":"/tmp","exit":0,"error":"no JSON"}],"skipped":[],"errors":[]}' ;;
  esac
  stub "$dir" sweep-worktrees.sh "$prune" "$sweep_out"
  if [[ "$prune" == timeout ]]; then
    printf '#!/usr/bin/env bash\nset -euo pipefail\necho "bounded-run: stand-in budget spent" >&2\nexit 124\n' > "$dir/bounded-run.sh" || die "write runner stub"
  else
    cp "$REAL/bounded-run.sh" "$dir/" || die "copy the bounded runner"
  fi
  stub "$dir" resolve-gates.sh 0 '{"instructions":["AGENTS.md"],"workflows":[],"runners":[]}'
  # The stub's `$*` and `$FOREMAN_TIER_*` expand when the stub runs, not here.
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\nset -euo pipefail\ncase "$*" in\n  *capability-check*) printf %s; exit 0 ;;\n  *measure*) printf %s; exit 0 ;;\n  *verify-foreman*) printf %%s "$FOREMAN_TIER_OUT"; exit "$FOREMAN_TIER_RC" ;;\nesac\nexit 9\n' \
    "'{\"due\":$due,\"entries\":0}'" "'{\"agents\":{}}'" > "$dir/foreman.sh" || die "write foreman stub"
  chmod +x "$dir/foreman.sh" || die "chmod foreman stub"
}

run() { # <dir> [extra args...]
  local dir="$1"; shift
  OUT="$(HERDR_ENV=fixture WORKTREE_ROOT="$TMP" bash "$dir/round-preflight.sh" \
    --repo owner/repo --checkout /tmp "$@" 2>"$ERRFILE")"
  RC=$?
  ERRTEXT="$(cat "$ERRFILE")"
}

# <json> <python-expression-over-d>, so a case reads one field or counts a list.
field() { printf '%s' "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(eval(sys.argv[1])))' "$2"; }

# An EXIT trap's final status becomes the script's, so cleanup ends on zero.
cleanup() { if [ -n "${TMP:-}" ]; then rm -rf "$TMP"; fi; return 0; }

main() {
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/preflight-tests.XXXXXX")" || die "mktemp"
  trap cleanup EXIT
  ERRFILE="$TMP/err"
  # What the stubbed `foreman verify-foreman` prints and exits with.
  # The shape `foreman verify-foreman` emits: the selected tier and its
  # process-argv proof.
  export FOREMAN_TIER_OUT='{"configured":true,"agent":"foreman","tier":{"model":"sonnet-5","effort":"low"},"argv_verified":true,"verified":{"source":"process_argv","argv":["claude","--model","sonnet-5","--effort","low"],"model":"sonnet-5","effort":"low","pid":400,"pane_id":"w1:p0"}}'
  export FOREMAN_TIER_RC=0

  echo "▶ the aggregate verdict" >&2

  shadow "$TMP/clean"
  run "$TMP/clean"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]] \
     && [[ "$(field "$OUT" 'd["blocking"]')" == "[]" ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["detail"]["tier"]["effort"]')" == '"low"' ]]; then
    pass; else fail "every check clean is ready, got RC=$RC OUT=$OUT"; fi

  # The foreman seat is verified like every other seat: a pane whose argv does
  # not carry the configured tier blocks the round and names the command.
  shadow "$TMP/badforeman"
  FOREMAN_TIER_RC=1 FOREMAN_TIER_OUT='' run "$TMP/badforeman"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"failed"' ]] \
     && printf '%s' "$OUT" | grep -q 'foreman verify-foreman exited 1'; then
    pass; else fail "an unproven foreman tier blocks the round, got RC=$RC OUT=$OUT"; fi

  # No `foreman` block is a visible warning, never a round block.
  shadow "$TMP/noforeman"
  FOREMAN_TIER_OUT='{"configured":false,"warning":"add a foreman block, then run start-foreman"}' run "$TMP/noforeman"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"unconfigured"' ]] \
     && printf '%s' "$ERRTEXT" | grep -q 'foreman seat is unconfigured'; then
    pass; else fail "an unconfigured foreman warns without blocking, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  shadow "$TMP/roster"
  OUT="$(HERDR_ENV='' WORKTREE_ROOT="$TMP" bash "$TMP/roster/round-preflight.sh" --repo o/r --checkout /tmp 2>"$ERRFILE")"
  RC=$?
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["mode"]["status"]')" == '"standalone"' ]] \
     && ! printf '%s' "$OUT" | grep -q '"roster"'; then
    pass; else fail "an unset HERDR_ENV stops before any other check, got RC=$RC OUT=$OUT"; fi

  echo "▶ what blocks a round" >&2

  shadow "$TMP/badroster" 1
  run "$TMP/badroster"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -q 'roster.sh exited 1'; then
    pass; else fail "a failing roster blocks and names its command, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/badauth" 0 2
  run "$TMP/badauth"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -q 'not permission'; then
    pass; else fail "a failing authority check blocks, got RC=$RC OUT=$OUT"; fi

  # A denial is a verdict the verifier emits on exit 0. It blocks as surely as
  # a failed check: exit status alone never grants authority.
  shadow "$TMP/denied" 0 0 0 false false
  run "$TMP/denied"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | grep -q 'does not own' \
     && [[ "$(field "$OUT" 'd["checks"]["authority"]["status"]')" == '"denied"' ]]; then
    pass; else fail "an authority denial on exit 0 blocks, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/both" 1 2
  run "$TMP/both"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'len(d["blocking"])')" == "2" ]] \
     && printf '%s' "$OUT" | grep -q 'roster.sh exited 1' \
     && printf '%s' "$OUT" | grep -q 'verify-authority.sh exited 2'; then
    pass; else fail "one failing check must not hide another, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune1" 0 0 1
  run "$TMP/prune1"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"undecided"' ]]; then
    pass; else fail "prune exit 1 is undecided, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune2" 0 0 2
  run "$TMP/prune2"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"failed"' ]]; then
    pass; else fail "prune exit 2 is failed, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune-other" 0 0 other
  run "$TMP/prune-other"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"degraded"' ]] \
     && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]]; then
    pass; else fail "another repository's failed prune must not block this round, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune-orphan" 0 0 orphan
  run "$TMP/prune-orphan"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"failed"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["detail"]["errors"][0]["path"]')" == '"/w/x"' ]]; then
    pass; else fail "an error naming no repository must block and keep the sweep detail, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune-mine" 0 0 mine
  run "$TMP/prune-mine"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"failed"' ]]; then
    pass; else fail "an error naming this checkout must block, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune-timeout" 0 0 timeout
  run "$TMP/prune-timeout"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"failed"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["reason"]')" == *budget* ]]; then
    pass; else fail "a sweep past its budget must block, got RC=$RC OUT=$OUT"; fi

  shadow "$TMP/prune-noresult" 0 0 noresult
  run "$TMP/prune-noresult"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["worktrees"]["status"]')" == '"failed"' ]]; then
    pass; else fail "this checkout's unreadable prune result must block, got RC=$RC OUT=$OUT"; fi

  # Exit 0 with valid JSON that is not an object is not the check's evidence.
  for shape in '[]' 'null' '7'; do
    shadow "$TMP/shape-$shape"
    stub "$TMP/shape-$shape" roster.sh 0 "$shape"
    stub "$TMP/shape-$shape" resolve-gates.sh 0 "$shape"
    run "$TMP/shape-$shape"
    # The backticks are literal Markdown in the reason, not command substitution.
    # shellcheck disable=SC2016
    if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "false" ]] \
       && [[ "$(field "$OUT" 'd["checks"]["roster"]["status"]')" == '"blocked"' ]] \
       && [[ "$(field "$OUT" 'd["checks"]["gates"]["status"]')" == '"blocked"' ]] \
       && [[ "$(field "$OUT" 'd["checks"]["roster"]["detail"]')" == "null" ]] \
       && printf '%s' "$OUT" | grep -q '`roster.sh` wrote JSON' \
       && printf '%s' "$OUT" | grep -q '`resolve-gates.sh /tmp` wrote JSON'; then
      pass; else fail "a '$shape' payload on exit 0 must block, got RC=$RC OUT=$OUT"; fi
  done

  # The authority and capability verdicts parse a field out of the payload; a
  # non-object there is an unreadable verdict, never a crash or a pass.
  shadow "$TMP/shape-authority"
  stub "$TMP/shape-authority" verify-authority.sh 0 '[]'
  run "$TMP/shape-authority"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["authority"]["status"]')" == '"failed"' ]] \
     && ! printf '%s' "$ERRTEXT" | grep -q 'Traceback'; then
    pass; else fail "a non-object authority payload must fail cleanly, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  shadow "$TMP/shape-capability"
  # The stub's `$*` and `$FOREMAN_TIER_*` expand when the stub runs, not here.
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\nset -euo pipefail\ncase "$*" in\n  *capability-check*) printf %s; exit 0 ;;\n  *measure*) printf %s; exit 0 ;;\n  *verify-foreman*) printf %%s "$FOREMAN_TIER_OUT"; exit "$FOREMAN_TIER_RC" ;;\nesac\nexit 9\n' \
    "'[]'" "'{\"agents\":{}}'" > "$TMP/shape-capability/foreman.sh" || die "write foreman stub"
  run "$TMP/shape-capability"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["capability"]["status"]')" == '"failed"' ]] \
     && ! printf '%s' "$ERRTEXT" | grep -q 'Traceback'; then
    pass; else fail "a non-object capability payload must fail cleanly, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  echo "▶ what is due without blocking" >&2

  shadow "$TMP/due" 0 0 0 true
  run "$TMP/due"
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]] \
     && printf '%s' "$OUT" | grep -q '"due": \["capability"\]'; then
    pass; else fail "a due cadence surfaces without blocking the round, got RC=$RC OUT=$OUT"; fi

  echo "▶ options and usage" >&2

  # --no-measure still verifies the foreman, on the latest saved snapshot.
  shadow "$TMP/nomeasure"
  run "$TMP/nomeasure" --no-measure
  if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["checks"]["headroom"]["status"]')" == '"skipped"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"ok"' ]]; then
    pass; else fail "--no-measure skips the one writing check and still verifies the foreman, got RC=$RC OUT=$OUT"; fi

  # The foreman's tier is selected on the measurement: a failed measure is a
  # recorded dependency failure, never a verification against a stale snapshot.
  shadow "$TMP/measurefails"
  # The stub's `$*` and `$FOREMAN_TIER_*` expand when the stub runs, not here.
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\nset -euo pipefail\ncase "$*" in\n  *capability-check*) printf %s; exit 0 ;;\n  *measure*) echo "measure: probe failed" >&2; exit 3 ;;\n  *verify-foreman*) printf %%s "$FOREMAN_TIER_OUT"; exit "$FOREMAN_TIER_RC" ;;\nesac\nexit 9\n' \
    "'{\"due\":false,\"entries\":0}'" > "$TMP/measurefails/foreman.sh" || die "write foreman stub"
  run "$TMP/measurefails"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["headroom"]["status"]')" == '"failed"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"failed"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"].get("detail")')" == "null" ]] \
     && printf '%s' "$OUT" | grep -q 'not verified: the foreman'; then
    pass; else fail "a failed measure records the foreman tier as a dependency failure, got RC=$RC OUT=$OUT"; fi

  # An absent `foreman` block is detected apart from headroom: a failed
  # measure still reports it as the unconfigured warning, never a block.
  FOREMAN_TIER_OUT='{"configured":false,"warning":"add a foreman block, then run start-foreman"}' run "$TMP/measurefails"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["headroom"]["status"]')" == '"failed"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"unconfigured"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"].get("reason")')" == "null" ]] \
     && ! printf '%s' "$OUT" | grep -q 'not verified: the foreman' \
     && printf '%s' "$ERRTEXT" | grep -q 'foreman seat is unconfigured'; then
    pass; else fail "an absent foreman block warns even when measure failed, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # A measure that exits 0 with unreadable output records headroom blocked;
  # the foreman's tier is still a dependency failure, never verified.
  shadow "$TMP/measurebadjson"
  # The stub's `$*` and `$FOREMAN_TIER_*` expand when the stub runs, not here.
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\nset -euo pipefail\ncase "$*" in\n  *capability-check*) printf %s; exit 0 ;;\n  *measure*) printf not-json; exit 0 ;;\n  *verify-foreman*) printf %%s "$FOREMAN_TIER_OUT"; exit "$FOREMAN_TIER_RC" ;;\nesac\nexit 9\n' \
    "'{\"due\":false,\"entries\":0}'" > "$TMP/measurebadjson/foreman.sh" || die "write foreman stub"
  run "$TMP/measurebadjson"
  if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["checks"]["headroom"]["status"]')" == '"blocked"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"failed"' ]] \
     && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"].get("detail")')" == "null" ]] \
     && printf '%s' "$OUT" | grep -q 'not verified: the foreman'; then
    pass; else fail "a zero-exit unreadable measure leaves the foreman tier unverified, got RC=$RC OUT=$OUT"; fi

  # foreman-tier-check.py owns the verdict: the preflight records its rows on
  # exit 0 or 1, and fails both rows on any other exit, unreadable output, or
  # rows that contradict the exit code (#626).
  local case_no=0 spec code json want
  local ok_rows='{"headroom": {"status": "ok", "detail": {"agents": {}}}, "foreman_tier": {"status": "ok", "detail": {}}}'
  local failed_rows='{"headroom": {"status": "ok", "detail": {"agents": {}}}, "foreman_tier": {"status": "failed", "reason": "stand-in: the tier is unproven"}}'
  for spec in \
    "1|$failed_rows|recorded" \
    "0|$ok_rows|ready" \
    "2|$ok_rows|both-failed" \
    "7|$ok_rows|both-failed" \
    "0|not json|both-failed" \
    '0|{"headroom": {"status": "ok"}}|both-failed' \
    "0|$failed_rows|both-failed" \
    "1|$ok_rows|both-failed"; do
    case_no=$((case_no + 1))
    code="${spec%%|*}"; json="${spec#*|}"; want="${json##*|}"; json="${json%|*}"
    shadow "$TMP/composite$case_no"
    printf '%s' "$json" > "$TMP/composite$case_no/composite.out" || die "write composite fixture"
    printf '#!/usr/bin/env python3\nimport pathlib, sys\nprint((pathlib.Path(__file__).parent / "composite.out").read_text())\nsys.exit(%s)\n' "$code" \
      > "$TMP/composite$case_no/foreman-tier-check.py" || die "write composite stub"
    run "$TMP/composite$case_no"
    case "$want" in
      ready)
        if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]] \
           && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"ok"' ]]; then
          pass; else fail "composite exit $code is ready, got RC=$RC OUT=$OUT"; fi ;;
      recorded)
        if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "false" ]] \
           && [[ "$(field "$OUT" 'd["checks"]["headroom"]["status"]')" == '"ok"' ]] \
           && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["reason"]')" == '"stand-in: the tier is unproven"' ]]; then
          pass; else fail "composite exit 1 records its rows, not ready, got RC=$RC OUT=$OUT"; fi ;;
      both-failed)
        if [[ $RC -eq 1 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "false" ]] \
           && [[ "$(field "$OUT" 'd["checks"]["headroom"]["status"]')" == '"failed"' ]] \
           && [[ "$(field "$OUT" 'd["checks"]["foreman_tier"]["status"]')" == '"failed"' ]] \
           && printf '%s' "$OUT" | grep -q "foreman-tier-check.py exited $code without a readable verdict"; then
          pass; else fail "composite exit $code with ($json) fails both rows, got RC=$RC OUT=$OUT"; fi ;;
    esac
  done

  shadow "$TMP/usage"
  for args in "--checkout /tmp" "--repo o/r"; do
    # shellcheck disable=SC2086  # deliberate word splitting of the fixture args
    OUT="$(HERDR_ENV=fixture WORKTREE_ROOT="$TMP" bash "$TMP/usage/round-preflight.sh" $args 2>"$ERRFILE")"
    RC=$?
    ERRTEXT="$(cat "$ERRFILE")"
    if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'round-preflight:'; then
      pass; else fail "missing '$args' counterpart is a usage error, got RC=$RC OUT=$OUT"; fi
  done

  echo "▶ each check's own payload survives" >&2

  shadow "$TMP/detail"
  run "$TMP/detail"
  if [[ "$(field "$OUT" 'd["checks"]["authority"]["detail"]["authorized"]')" == "true" ]] \
     && [[ "$(field "$OUT" 'd["checks"]["capability"]["detail"]["entries"]')" == "0" ]]; then
    pass; else fail "a check's own payload must reach the caller, got OUT=$OUT"; fi

  # Resolved once here so five workers do not each spend turns finding the same
  # files; the foreman renders it into the briefs' shared GATES value.
  if [[ "$(field "$OUT" 'd["checks"]["gates"]["detail"]["instructions"]')" == '["AGENTS.md"]' ]]; then
    pass; else fail "gate pointers must reach the caller for the briefs, got OUT=$OUT"; fi

  echo "▶ a directory name ending in a newline" >&2

  # A `$(dirname ...)` capture drops the newline, and every collaborator is
  # then looked for beside a directory that does not exist (#592).
  if mkdir "$TMP/nl-probe"$'\n' 2>"$TMP/nl.err"; then
    shadow "$TMP/nl"$'\n'
    run "$TMP/nl"$'\n'
    if [[ $RC -eq 0 ]] && [[ "$(field "$OUT" 'd["ready"]')" == "true" ]]; then
      pass; else fail "a newline-named plugin dir still reaches its collaborators, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  else
    echo "  skipped: this filesystem refuses a name ending in a newline ($(cat "$TMP/nl.err"))" >&2
  fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
