#!/usr/bin/env bash
# Outcome-based tests for scaffold.sh's ensure_gitattributes_marker
# function — covers the trailing-newline guarantee per rules/code-
# formatting.md "End files with a single newline" basic, AND the
# repair-on-rerun behavior (an existing consumer with marker-present
# but missing trailing newline gets fixed by re-running scaffold).
#
# Approach: source scaffold.sh (the main() guard prevents auto-run
# when sourced) and call the function directly against a tempfile.
# Each test constructs the precondition fixture, calls the function,
# and inspects the resulting file with byte-level assertions so a
# silent off-by-one or wrong-newline-count regression surfaces loudly.
#
# Portability: avoids `shasum` and `xxd` (not POSIX, not universally
# installed in minimal environments) — uses `cksum` and `od` instead
# so the suite runs on bare alpine, BSD, and stripped CI images.
#
# Run: bash skills/install-reviewer/tests/test_scaffold_gitattributes.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/scaffold.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: scaffold.sh not executable at $SCRIPT" >&2; exit 2; }

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

RULE='.github/workflows/*.lock.yml linguist-generated=true merge=ours'

FAIL_COUNT=0
PASS_COUNT=0

TMPDIR_TEST=$(mktemp -d -t scaffold-gitattr-test.XXXXXX)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# Returns the file's last byte as a 2-char lowercase hex string (e.g.,
# "0a" for \n, "73" for 's'). POSIX-portable via od + tr.
last_byte_hex() {
  tail -c 1 "$1" | od -An -tx1 | tr -d ' \n'
}

# Returns the count of trailing newline bytes (greedy from EOF backward).
trailing_newline_count() {
  perl -e 'local $/; my $s = <STDIN>; my ($t) = $s =~ /(\n*)\z/; print length($t);' < "$1"
}

# Returns a fingerprint suitable for "content unchanged" comparison.
content_fingerprint() {
  cksum < "$1"
}

# Counts occurrences of the marker line (verifies no duplicate appended).
marker_count() {
  grep -cxF "$RULE" "$1" 2>/dev/null || echo 0
}

# --- Test 1: fresh consumer (file doesn't exist) -----------------------------
test_fresh_file() {
  local f="$TMPDIR_TEST/fresh"
  rm -f "$f"
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  [[ -f "$f" ]] || { echo "    FAIL: file not created" >&2; return 1; }
  assert_eq "marker count" "1" "$(marker_count "$f")" || return 1
  assert_eq "last byte" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "fresh consumer: file created with marker + single trailing newline" test_fresh_file

# --- Test 2: existing file WITH trailing newline, marker absent --------------
test_existing_with_newline() {
  local f="$TMPDIR_TEST/with-nl"
  printf '*.log\n*.tmp\n' > "$f"
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  assert_eq "marker count" "1" "$(marker_count "$f")" || return 1
  grep -qxF '*.log' "$f" || { echo "    FAIL: existing content clobbered" >&2; return 1; }
  assert_eq "last byte" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "existing file with newline: marker appended; single trailing newline" test_existing_with_newline

# --- Test 3: existing file WITHOUT trailing newline, marker absent -----------
test_existing_no_newline() {
  local f="$TMPDIR_TEST/no-nl"
  printf '*.log\n*.tmp' > "$f"
  assert_eq "fixture last byte (precondition)" "70" "$(last_byte_hex "$f")" || return 1
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  assert_eq "marker count" "1" "$(marker_count "$f")" || return 1
  grep -qxF '*.tmp' "$f" || { echo "    FAIL: pre-existing last line lost" >&2; return 1; }
  assert_eq "last byte" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "existing file without newline: newline inserted before marker; single trailing newline" test_existing_no_newline

# --- Test 4: marker present + ends with newline → file unchanged -------------
test_marker_present_clean() {
  local f="$TMPDIR_TEST/clean-marker"
  printf '*.log\n%s\n' "$RULE" > "$f"
  local fp_before
  fp_before=$(content_fingerprint "$f")
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local fp_after
  fp_after=$(content_fingerprint "$f")
  assert_eq "content fingerprint" "$fp_before" "$fp_after" || return 1
  assert_eq "marker count (no duplicate)" "1" "$(marker_count "$f")" || return 1
}
run "marker present + ends with newline: file untouched" test_marker_present_clean

# --- Test 5: marker present + MISSING trailing newline → repaired ------------
# Repair-on-rerun: a consumer whose .gitattributes was scaffolded by a
# buggy older scaffold (marker landed, newline didn't) re-runs the
# install-reviewer skill; the helper must converge the file to single-
# trailing-newline state without duplicating the marker.
test_marker_present_missing_newline() {
  local f="$TMPDIR_TEST/marker-no-nl"
  printf '*.log\n%s' "$RULE" > "$f"
  assert_eq "fixture last byte (precondition: non-newline)" "73" "$(last_byte_hex "$f")" || return 1
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  assert_eq "marker count (no duplicate)" "1" "$(marker_count "$f")" || return 1
  assert_eq "last byte after repair" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "marker present + missing newline: REPAIRED via re-run (no duplicate marker)" test_marker_present_missing_newline

# --- Test 6: idempotent across two invocations on canonical state ------------
test_idempotency() {
  local f="$TMPDIR_TEST/idempotent"
  rm -f "$f"
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local fp_first
  fp_first=$(content_fingerprint "$f")
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local fp_second
  fp_second=$(content_fingerprint "$f")
  assert_eq "second invocation no-op on canonical state" "$fp_first" "$fp_second" || return 1
}
run "idempotent across invocations on canonical state" test_idempotency

# --- Test 7: idempotent on repaired state ------------------------------------
# After the helper repairs a marker-present-no-newline file, a second
# call must be a no-op (the repair brought the file to canonical state).
test_idempotency_after_repair() {
  local f="$TMPDIR_TEST/idem-after-repair"
  printf '*.log\n%s' "$RULE" > "$f"
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local fp_after_repair
  fp_after_repair=$(content_fingerprint "$f")
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local fp_second
  fp_second=$(content_fingerprint "$f")
  assert_eq "no-op after repair" "$fp_after_repair" "$fp_second" || return 1
  assert_eq "marker count" "1" "$(marker_count "$f")" || return 1
}
run "idempotent after marker-present repair" test_idempotency_after_repair

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
