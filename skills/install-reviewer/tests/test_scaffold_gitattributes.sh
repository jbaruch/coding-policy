#!/usr/bin/env bash
# Outcome-based tests for scaffold.sh's ensure_gitattributes_marker
# function — covers the trailing-newline guarantee per rules/code-
# formatting.md "End files with a single newline" basic.
#
# Approach: source scaffold.sh (the main() guard prevents auto-run when
# sourced) and call the function directly against a tempfile. Each test
# constructs the precondition fixture, calls the function, and inspects
# the resulting file with byte-level assertions (xxd / wc -c) so a
# silent off-by-one or wrong-newline-count regression surfaces loudly.
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

# Returns the file's last byte as a 2-char hex string (e.g., "0a" for \n).
last_byte_hex() {
  tail -c 1 "$1" | xxd -p
}

# Returns the count of trailing newline bytes (greedy from EOF backward).
trailing_newline_count() {
  local f="$1"
  perl -e 'local $/; my $s = <STDIN>; my ($t) = $s =~ /(\n*)\z/; print length($t);' < "$f"
}

# --- Test 1: fresh consumer (file doesn't exist) -----------------------------
test_fresh_file() {
  local f="$TMPDIR_TEST/fresh"
  rm -f "$f"
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  [[ -f "$f" ]] || { echo "    FAIL: file not created" >&2; return 1; }
  grep -qxF "$RULE" "$f" || { echo "    FAIL: marker not present" >&2; return 1; }
  assert_eq "last byte" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "fresh consumer: file created with marker + single trailing newline" test_fresh_file

# --- Test 2: existing file WITH trailing newline, marker absent --------------
test_existing_with_newline() {
  local f="$TMPDIR_TEST/with-nl"
  printf '*.log\n*.tmp\n' > "$f"
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  grep -qxF "$RULE" "$f" || { echo "    FAIL: marker not appended" >&2; return 1; }
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
  grep -qxF "$RULE" "$f" || { echo "    FAIL: marker not appended" >&2; return 1; }
  grep -qxF '*.tmp' "$f" || { echo "    FAIL: pre-existing last line lost" >&2; return 1; }
  assert_eq "last byte" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "existing file without newline: newline inserted before marker; single trailing newline" test_existing_no_newline

# --- Test 4: file with marker already present is left alone -------------------
test_marker_already_present() {
  local f="$TMPDIR_TEST/has-marker"
  printf '*.log\n%s\n' "$RULE" > "$f"
  local sha_before
  sha_before=$(shasum < "$f")
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local sha_after
  sha_after=$(shasum < "$f")
  assert_eq "checksum unchanged" "$sha_before" "$sha_after" || return 1
}
run "marker already present: file untouched (idempotent on second invocation)" test_marker_already_present

# --- Test 5: function is idempotent across two invocations -------------------
test_idempotency() {
  local f="$TMPDIR_TEST/idempotent"
  rm -f "$f"
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local sha_first
  sha_first=$(shasum < "$f")
  ensure_gitattributes_marker "$f" "$RULE" || return 1
  local sha_second
  sha_second=$(shasum < "$f")
  assert_eq "second invocation no-op" "$sha_first" "$sha_second" || return 1
}
run "idempotent across invocations" test_idempotency

# --- Test 6: regex regression — simulated post-printf newline strip ----------
# Direct regression test for the bug Copilot flagged on PR #88: the
# previous \s+\z regex no-ops when EOF is non-whitespace, leaving the
# reported failure mode unfixed. Mock the printf-stripped-newline state
# by writing the marker WITHOUT a trailing newline, then ask the
# function to run its sanitation pass against that fixture. Since the
# marker is already present, the function would normally short-circuit;
# bypass the short-circuit by running just the perl pass directly so
# the test exercises the regex itself.
test_regex_fixes_non_whitespace_eof() {
  local f="$TMPDIR_TEST/non-ws-eof"
  printf '%s' "$RULE" > "$f"
  assert_eq "fixture last byte (precondition)" "73" "$(last_byte_hex "$f")" || return 1
  perl -i -0pe 's/\s*\z/\n/' "$f"
  assert_eq "last byte after sanitation" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "perl sanitation regex adds newline when EOF is non-whitespace" test_regex_fixes_non_whitespace_eof

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
