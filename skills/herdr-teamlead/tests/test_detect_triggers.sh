#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/detect-triggers.sh.
#
# Real local git repos, built per scenario, so the cases share no state and
# run in any order (rules/testing-standards.md Independence).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. New package          -> architect fires on a package the base lacked.
#   2. Large package        -> architect fires above the declared threshold.
#   3. Small package        -> a change under the threshold is quiet.
#   4. Trust boundary       -> security fires on a declared path.
#   5. Command surface      -> ux_product fires on a modified declared path.
#   6. Added document       -> documentation fires only on an added path.
#   7. Modified document    -> an edit to an existing document is quiet.
#   8. Undeclared section   -> reported undeclared, never fired.
#   9. No config            -> exit 1; a repo that declared nothing decides nothing.
#  10. Malformed config     -> exit 1 naming the defect, for four shapes.
#  11. Bad refs / usage     -> exit 1 with no JSON.
#  12. Newline in a path    -> one record, not two.
#  13. Binary file          -> counted as changed with unknown size.
#
# Run: bash skills/herdr-teamlead/tests/test_detect_triggers.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

commit() { # <repo> <message>
  git -C "$1" add -A                                        || die "git add failed in $1"
  git -C "$1" -c user.name=t -c user.email=t@t commit -qm "$2" || die "git commit failed in $1"
}

write_config() { # <repo> <json>
  printf '%s\n' "$2" > "$1/.herdr-triggers.json" || die "config write failed"
}

mk_repo() { # <name> [config-json] -> sets REPO
  REPO="$TMP/$1"
  mkdir -p "$REPO" || die "mkdir failed"
  git -C "$REPO" init -q -b main . || die "git init failed"
  printf 'seed\n' > "$REPO/README.md" || die "seed write failed"
  mkdir -p "$REPO/src/cli" "$REPO/docs" "$REPO/internal/one" || die "mkdir failed"
  printf 'a\n' > "$REPO/src/cli/main.py"   || die "write failed"
  printf 'b\n' > "$REPO/internal/one/a.py" || die "write failed"
  printf 'old\n' > "$REPO/docs/existing.md" || die "write failed"
  if (( $# > 1 )); then write_config "$REPO" "$2"; fi
  commit "$REPO" base
}

FULL_CONFIG='{"schema_version": 1,
 "architect": {"package_globs": ["internal/*"], "changed_lines": 5},
 "security": {"paths": ["**/auth/**"]},
 "ux_product": {"paths": ["src/cli/**"]},
 "documentation": {"paths": ["docs/**"]}}'

run() { # <args...>
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

fired() { python3 -c 'import json,sys; print(" ".join(r["trigger"] for r in json.load(sys.stdin)["fired"]))' <<<"$OUT"; }
quiet() { python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)["quiet"]))' <<<"$OUT"; }
undeclared() { python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)["undeclared"]))' <<<"$OUT"; }
evidence() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(" ".join(sorted(e for r in d["fired"] if r["trigger"]==sys.argv[1] for e in r["evidence"])))' "$1" <<<"$OUT"; }
field() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1" <<<"$OUT"; }

main() {
  PASS=0; FAIL=0; RUN_SEQ=0
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/detect-triggers.sh"
  [[ -f "$SCRIPT" ]] || die "script not found: $SCRIPT"
  TMP="$(mktemp -d)" || die "mktemp failed"
  TMP="$(cd "$TMP" && pwd -P)" || die "resolve TMP failed"
  trap cleanup EXIT

  # --- 1, 4, 5, 6, 7: one repo, one run decides them all.
  mk_repo one "$FULL_CONFIG"
  mkdir -p "$REPO/internal/two" "$REPO/src/auth" || die "mkdir failed"
  printf 'new package\n' > "$REPO/internal/two/b.py" || die "write failed"
  printf 'token check\n' > "$REPO/src/auth/verify.py" || die "write failed"
  printf 'a\nflag\n' > "$REPO/src/cli/main.py" || die "write failed"
  printf 'guide\n' > "$REPO/docs/guide.md" || die "write failed"
  printf 'old\nedited\n' > "$REPO/docs/existing.md" || die "write failed"
  commit "$REPO" head
  run "$REPO" HEAD~1 HEAD
  echo "1. a package the base lacked fires the architect"
  if (( RC == 0 )) && [[ "$(evidence architect)" == "internal/two" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "4. a changed trust-boundary path fires security"
  if [[ "$(evidence security)" == "src/auth/verify.py" ]]; then pass; else fail "out=$OUT"; fi
  echo "5. a modified command-surface path fires UX and product"
  if [[ "$(evidence ux_product)" == "src/cli/main.py" ]]; then pass; else fail "out=$OUT"; fi
  echo "6. an added document fires documentation"
  if [[ "$(evidence documentation)" == "docs/guide.md" ]]; then pass; else fail "out=$OUT"; fi
  echo "7. an edit to an existing document does not"
  if [[ "$(evidence documentation)" != *existing.md* ]]; then pass; else fail "out=$OUT"; fi

  # --- 2, 3: the declared threshold decides an existing package.
  mk_repo two "$FULL_CONFIG"
  printf 'b\n1\n2\n3\n4\n5\n6\n' > "$REPO/internal/one/a.py" || die "write failed"
  commit "$REPO" big
  run "$REPO" HEAD~1 HEAD
  echo "2. an existing package above the threshold fires the architect"
  if (( RC == 0 )) && [[ "$(evidence architect)" == "internal/one" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  mk_repo three "$FULL_CONFIG"
  printf 'b\n1\n' > "$REPO/internal/one/a.py" || die "write failed"
  commit "$REPO" small
  run "$REPO" HEAD~1 HEAD
  echo "3. an existing package under the threshold is quiet"
  if (( RC == 0 )) && [[ "$(quiet)" == *architect* ]] && [[ "$(fired)" != *architect* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 8: an omitted section is undeclared, never quiet and never fired.
  mk_repo eight '{"schema_version": 1, "security": {"paths": ["**/auth/**"]}}'
  mkdir -p "$REPO/internal/nine" || die "mkdir failed"
  printf 'x\n' > "$REPO/internal/nine/c.py" || die "write failed"
  commit "$REPO" head
  run "$REPO" HEAD~1 HEAD
  echo "8. an omitted section reports undeclared and never fires"
  if (( RC == 0 )) && [[ "$(undeclared)" == "architect documentation ux_product" ]] && [[ "$(fired)" == "" ]] && [[ "$(quiet)" == "security" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 9: no config decides nothing.
  mk_repo nine
  printf 'y\n' > "$REPO/src/cli/main.py" || die "write failed"
  commit "$REPO" head
  run "$REPO" HEAD~1 HEAD
  echo "9. a repo with no trigger config is exit 1 with no JSON"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"declares nothing"* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 10: each malformed shape names its own defect.
  mk_repo ten
  printf 'y\n' > "$REPO/src/cli/main.py" || die "write failed"
  commit "$REPO" head
  local bad_ok=1
  for bad in \
    'not json' \
    '{"schema_version": 2}' \
    '{"schema_version": 1, "typo": {"paths": ["x"]}}' \
    '{"schema_version": 1, "architect": {"package_globs": ["x"], "changed_lines": 0}}' \
    '{"schema_version": 1, "security": {"paths": []}}'
  do
    write_config "$REPO" "$bad"
    run "$REPO" HEAD~1 HEAD
    if (( RC != 1 )) || [[ -n "$OUT" ]]; then bad_ok=0; echo "    unexpected for: $bad (rc=$RC out=$OUT)" >&2; fi
  done
  echo "10. every malformed config shape is exit 1 with no JSON"
  if (( bad_ok )); then pass; else fail "see above"; fi

  # --- 11: bad refs and usage.
  mk_repo eleven "$FULL_CONFIG"
  printf 'y\n' > "$REPO/README.md" || die "write failed"
  commit "$REPO" head
  run "$REPO" HEAD~1 no-such-ref
  echo "11a. an unresolvable head ref is exit 1"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"cannot resolve head ref"* ]]; then pass; else fail "rc=$RC err=$ERRTEXT"; fi
  run "$REPO"
  echo "11b. usage is exit 1 with no JSON"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *usage* ]]; then pass; else fail "rc=$RC err=$ERRTEXT"; fi
  run "$TMP" HEAD~1 HEAD
  echo "11c. a non-repository is exit 1"
  if (( RC == 1 )) && [[ -z "$OUT" ]]; then pass; else fail "rc=$RC out=$OUT"; fi

  # --- 12: a newline in a path stays one record.
  mk_repo twelve "$FULL_CONFIG"
  printf 'x\n' > "$REPO/docs/$(printf 'a\nb').md" || die "newline write failed"
  commit "$REPO" head
  run "$REPO" HEAD~1 HEAD
  echo "12. a newline in a path does not split its record"
  if (( RC == 0 )) && [[ "$(field changed)" == 1 ]] && [[ "$(fired)" == "documentation" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 13: a binary file has no line counts.
  mk_repo thirteen "$FULL_CONFIG"
  printf '\x00\x01\x02binary\x00' > "$REPO/internal/one/blob.bin" || die "binary write failed"
  commit "$REPO" head
  run "$REPO" HEAD~1 HEAD
  echo "13. a binary file counts as changed without line counts"
  if (( RC == 0 )) && [[ "$(field changed)" == 1 ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  echo
  echo "passed=$PASS failed=$FAIL"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
