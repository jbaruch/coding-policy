#!/usr/bin/env bash
# Outcome-based tests for scaffold.sh's .env.example handling — the
# parse_repo_slug_from_url URL parser and the ensure_env_example
# create-or-merge function that documents reviewer CI secrets per
# rules/no-secrets.md.
#
# Approach: source scaffold.sh (the main() guard prevents auto-run when
# sourced) and call the functions directly. parse_repo_slug_from_url is
# a pure function — no git calls — so the enumerable URL-form parsing is
# asserted in isolation. ensure_env_example is exercised against
# tempfiles with byte-level / content assertions so a regression (header
# re-appended on a no-op, an existing consumer key clobbered, missing
# trailing newline) surfaces loudly.
#
# Portability: byte-level assertions use POSIX `cksum` and `od` (same as
# test_scaffold_gitattributes.sh).
#
# Run: bash skills/install-reviewer/tests/test_scaffold_env_example.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/scaffold.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: scaffold.sh not executable at $SCRIPT" >&2; exit 2; }

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

SLUG="acme/widgets"
SECRETS=(CODEX_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY TESSL_TOKEN)
DEEP_LINK="#   https://github.com/${SLUG}/settings/secrets/actions"

FAIL_COUNT=0
PASS_COUNT=0

TMPDIR_TEST=$(mktemp -d -t scaffold-env-test.XXXXXX)
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

last_byte_hex() {
  tail -c 1 "$1" | od -An -tx1 | tr -d ' \n'
}

trailing_newline_count() {
  perl -e 'local $/; my $s = <STDIN>; my ($t) = $s =~ /(\n*)\z/; print length($t);' < "$1"
}

content_fingerprint() {
  cksum < "$1"
}

key_count() {
  grep -cE "^$2=" "$1" 2>/dev/null || true
}

# Asserts every reviewer secret has exactly one KEY= line and the deep
# link header is present.
assert_complete_and_linked() {
  local f="$1" k
  for k in "${SECRETS[@]}"; do
    assert_eq "key ${k} count" "1" "$(key_count "$f" "$k")" || return 1
  done
  grep -qxF "$DEEP_LINK" "$f" || { echo "    FAIL: deep link header missing" >&2; return 1; }
}

# --- parse_repo_slug_from_url: the three URL forms git emits -----------------
test_parse_https() {
  assert_eq "https + .git" "acme/widgets" "$(parse_repo_slug_from_url 'https://github.com/acme/widgets.git')" || return 1
  assert_eq "https no .git" "acme/widgets" "$(parse_repo_slug_from_url 'https://github.com/acme/widgets')" || return 1
}
run "parse_repo_slug_from_url: HTTPS forms" test_parse_https

test_parse_scp() {
  assert_eq "scp + .git" "acme/widgets" "$(parse_repo_slug_from_url 'git@github.com:acme/widgets.git')" || return 1
  assert_eq "scp no .git" "acme/widgets" "$(parse_repo_slug_from_url 'git@github.com:acme/widgets')" || return 1
}
run "parse_repo_slug_from_url: SCP-style SSH forms" test_parse_scp

test_parse_ssh_url() {
  assert_eq "ssh:// + .git" "acme/widgets" "$(parse_repo_slug_from_url 'ssh://git@github.com/acme/widgets.git')" || return 1
}
run "parse_repo_slug_from_url: ssh:// URL form" test_parse_ssh_url

test_parse_trailing_slash() {
  assert_eq "trailing slash" "acme/widgets" "$(parse_repo_slug_from_url 'https://github.com/acme/widgets/')" || return 1
  # `.git/` — a remote URL copied with a trailing slash after .git.
  assert_eq "https .git/" "acme/widgets" "$(parse_repo_slug_from_url 'https://github.com/acme/widgets.git/')" || return 1
  assert_eq "scp .git/" "acme/widgets" "$(parse_repo_slug_from_url 'git@github.com:acme/widgets.git/')" || return 1
}
run "parse_repo_slug_from_url: trailing slash and .git/ stripped" test_parse_trailing_slash

test_parse_rejects_empty_and_bare() {
  parse_repo_slug_from_url "" && { echo "    FAIL: empty URL accepted" >&2; return 1; }
  parse_repo_slug_from_url "widgets" && { echo "    FAIL: single-token URL accepted" >&2; return 1; }
  return 0
}
run "parse_repo_slug_from_url: rejects empty and single-token input" test_parse_rejects_empty_and_bare

# --- ensure_env_example: fresh consumer (file absent) ------------------------
test_env_fresh() {
  local f="$TMPDIR_TEST/fresh.env"
  rm -f "$f"
  ensure_env_example "$f" "$SLUG" || return 1
  [[ -f "$f" ]] || { echo "    FAIL: file not created" >&2; return 1; }
  assert_complete_and_linked "$f" || return 1
  assert_eq "last byte" "0a" "$(last_byte_hex "$f")" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "ensure_env_example: fresh file gets all secrets + deep link" test_env_fresh

# --- ensure_env_example: merge into existing file, preserve content ----------
test_env_merge_preserves() {
  local f="$TMPDIR_TEST/existing.env"
  printf 'DATABASE_URL=postgres://localhost/app\nANTHROPIC_API_KEY=sk-existing\n' > "$f"
  ensure_env_example "$f" "$SLUG" || return 1
  # Consumer's own var and pre-set ANTHROPIC key preserved verbatim.
  grep -qxF 'DATABASE_URL=postgres://localhost/app' "$f" || { echo "    FAIL: consumer var clobbered" >&2; return 1; }
  grep -qxF 'ANTHROPIC_API_KEY=sk-existing' "$f" || { echo "    FAIL: pre-set key clobbered" >&2; return 1; }
  # Present key not duplicated; missing keys appended.
  assert_eq "ANTHROPIC_API_KEY count (no duplicate)" "1" "$(key_count "$f" ANTHROPIC_API_KEY)" || return 1
  assert_eq "CODEX_API_KEY appended" "1" "$(key_count "$f" CODEX_API_KEY)" || return 1
  assert_eq "OPENAI_API_KEY appended" "1" "$(key_count "$f" OPENAI_API_KEY)" || return 1
  assert_eq "TESSL_TOKEN appended" "1" "$(key_count "$f" TESSL_TOKEN)" || return 1
  grep -qxF "$DEEP_LINK" "$f" || { echo "    FAIL: deep link header missing" >&2; return 1; }
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "ensure_env_example: merges missing keys, preserves existing content" test_env_merge_preserves

# --- ensure_env_example: deep link lands in the header (above vars) ----------
# rules/no-secrets.md requires the deep link "in the file header". When
# merging into a consumer file that has variables but no link, the block
# must be PREPENDED so the link sits above the first variable line.
test_env_link_in_header_on_merge() {
  local f="$TMPDIR_TEST/header-placement.env"
  printf 'DATABASE_URL=postgres://localhost/app\nREDIS_URL=redis://localhost\n' > "$f"
  ensure_env_example "$f" "$SLUG" || return 1
  local link_line var_line
  link_line=$(grep -nF 'settings/secrets/actions' "$f" | head -1 | cut -d: -f1)
  var_line=$(grep -nE '^DATABASE_URL=' "$f" | head -1 | cut -d: -f1)
  [[ -n "$link_line" && -n "$var_line" ]] || { echo "    FAIL: missing link ($link_line) or var ($var_line) line" >&2; return 1; }
  [[ "$link_line" -lt "$var_line" ]] || { echo "    FAIL: deep link (line $link_line) not before first var (line $var_line)" >&2; return 1; }
  grep -qxF 'DATABASE_URL=postgres://localhost/app' "$f" || { echo "    FAIL: consumer var clobbered" >&2; return 1; }
  grep -qxF 'REDIS_URL=redis://localhost' "$f" || { echo "    FAIL: consumer var clobbered" >&2; return 1; }
  assert_complete_and_linked "$f" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "ensure_env_example: deep link prepended into header on merge" test_env_link_in_header_on_merge

# --- ensure_env_example: existing file WITHOUT trailing newline --------------
test_env_merge_no_newline() {
  local f="$TMPDIR_TEST/no-nl.env"
  printf 'FOO=bar' > "$f"
  assert_eq "fixture last byte (precondition: non-newline)" "72" "$(last_byte_hex "$f")" || return 1
  ensure_env_example "$f" "$SLUG" || return 1
  grep -qxF 'FOO=bar' "$f" || { echo "    FAIL: last line lost" >&2; return 1; }
  assert_complete_and_linked "$f" || return 1
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "ensure_env_example: existing file without newline merges cleanly" test_env_merge_no_newline

# --- ensure_env_example: all keys present AND link present → untouched -------
test_env_idempotent_noop() {
  local f="$TMPDIR_TEST/complete.env"
  {
    printf '#   https://github.com/%s/settings/secrets/actions\n' "$SLUG"
    printf 'CODEX_API_KEY=\nOPENAI_API_KEY=\nANTHROPIC_API_KEY=\nTESSL_TOKEN=\n'
  } > "$f"
  local fp_before
  fp_before=$(content_fingerprint "$f")
  ensure_env_example "$f" "$SLUG" || return 1
  local fp_after
  fp_after=$(content_fingerprint "$f")
  assert_eq "content fingerprint unchanged" "$fp_before" "$fp_after" || return 1
  # No duplicate header appended.
  assert_eq "deep-link header count" "1" "$(grep -cF 'settings/secrets/actions' "$f")" || return 1
}
run "ensure_env_example: all keys + link present → file untouched" test_env_idempotent_noop

# --- ensure_env_example: all keys present, link MISSING → header backfilled --
# rules/no-secrets.md requires the deep link "in the file header". A
# consumer file that documents every reviewer secret but lacks the link
# must still get the link appended — not left non-compliant.
test_env_backfills_missing_header() {
  local f="$TMPDIR_TEST/keys-no-link.env"
  printf 'CODEX_API_KEY=\nOPENAI_API_KEY=\nANTHROPIC_API_KEY=\nTESSL_TOKEN=\n' > "$f"
  assert_eq "precondition: no link" "0" "$(grep -cF 'settings/secrets/actions' "$f")" || return 1
  ensure_env_example "$f" "$SLUG" || return 1
  assert_eq "deep-link header appended" "1" "$(grep -cF 'settings/secrets/actions' "$f")" || return 1
  grep -qxF "$DEEP_LINK" "$f" || { echo "    FAIL: deep link line missing" >&2; return 1; }
  # No secret key duplicated — they were all already present.
  for k in "${SECRETS[@]}"; do
    assert_eq "key ${k} count (no duplicate)" "1" "$(key_count "$f" "$k")" || return 1
  done
  assert_eq "trailing newline count" "1" "$(trailing_newline_count "$f")" || return 1
}
run "ensure_env_example: backfills missing deep-link header" test_env_backfills_missing_header

# --- ensure_env_example: idempotent across two invocations on fresh file -----
test_env_idempotency() {
  local f="$TMPDIR_TEST/idem.env"
  rm -f "$f"
  ensure_env_example "$f" "$SLUG" || return 1
  local fp_first
  fp_first=$(content_fingerprint "$f")
  ensure_env_example "$f" "$SLUG" || return 1
  local fp_second
  fp_second=$(content_fingerprint "$f")
  assert_eq "second invocation no-op" "$fp_first" "$fp_second" || return 1
  for k in "${SECRETS[@]}"; do
    assert_eq "key ${k} count after re-run" "1" "$(key_count "$f" "$k")" || return 1
  done
}
run "ensure_env_example: idempotent across invocations" test_env_idempotency

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
