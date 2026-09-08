#!/usr/bin/env bash
# Outcome-based tests for verify-github-release.sh.
#
# Covers the answers the script promises:
#   1. Published release, every asset uploaded — rc 0, ok true, the
#      asset count and URL carried through.
#   2. No release at the tag (HTTP 404) — rc 1, ok false, a reason
#      naming the tag. A definitive no, never an error.
#   3. Draft release — rc 1, ok false. A green publish run that left a
#      draft behind is not a landed publication.
#   4. Zero assets — rc 1, ok false. An empty release never passes
#      vacuously.
#   5. An asset still uploading — rc 1, ok false, naming the counts.
#   6. Tag mismatch — the payload reports a different tag; rc 1.
#   7. Auth or network failure — rc 2, empty stdout. Indeterminate is
#      never reported as absent (fail closed).
#   8. Unparseable payload — rc 2, empty stdout.
#   9. Argument validation — wrong count and empty arguments exit 2.
#  10. Missing gh — exit 2 with an install hint.
#
# Approach: source the script (the main() guard prevents auto-run when
# sourced) and override `gh` as a transport mock. Each case writes a
# fixture holding a raw `GET /repos/{o}/{r}/releases/tags/{tag}` body,
# and the mock runs the caller's own `--jq` filter over it with jq, the
# way gh does. The script's conjunction is what the fixtures exercise.
#
# Run: bash skills/release/tests/test_verify_github_release.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/verify-github-release.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: verify-github-release.sh not executable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null || { echo "fatal: jq is required — the transport mock filters fixtures with it" >&2; exit 2; }

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

OWNER=jbaruch
REPO=good-oss-citizen
TAG=v0.1.4

TMPDIR_TEST=$(mktemp -d -t verify-gh-release-test.XXXXXX)
cleanup_tmp() {
  if [[ -n "${TMPDIR_TEST:-}" ]]; then
    if ! rm -rf "$TMPDIR_TEST"; then
      echo "warning: could not remove temp dir ${TMPDIR_TEST} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_tmp EXIT
export MOCK_BODY_FILE="$TMPDIR_TEST/body.json"
export MOCK_MODE_FILE="$TMPDIR_TEST/mode"

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

# Mock `gh` — transport stand-in for `gh api <path> --jq '<filter>'`.
# MODE selects the transport outcome: `ok` serves the fixture body
# through the caller's own filter, `404` and `auth` reproduce gh's
# non-zero exit and its stderr text for those failures.
gh() {
  [[ "$1" == "api" ]] || { echo "mock gh: unexpected invocation: $*" >&2; return 99; }
  local mode
  mode=$(cat "$MOCK_MODE_FILE")
  case "$mode" in
    404)
      echo "gh: Not Found (HTTP 404)" >&2
      return 1
      ;;
    auth)
      echo "gh: HTTP 401: Bad credentials" >&2
      return 1
      ;;
  esac
  local filter="" prev="" arg
  for arg in "$@"; do
    [[ "$prev" == "--jq" ]] && filter="$arg"
    prev="$arg"
  done
  [[ -n "$filter" ]] || { echo "mock gh: no --jq filter passed" >&2; return 99; }
  jq -r "$filter" "$MOCK_BODY_FILE"
}

set_mode() { echo "$1" > "$MOCK_MODE_FILE"; }
set_body() { cat > "$MOCK_BODY_FILE"; set_mode ok; }

# `.ok // empty` cannot be used: jq's `//` treats `false` as absent, so
# a correct `"ok":false` would read as no field at all.
ok_of() { echo "$1" | jq -r 'if has("ok") then (.ok|tostring) else empty end'; }
reason_of() { echo "$1" | jq -r 'if has("reason") then .reason else empty end'; }

# --- Test 1: published release with retrievable assets -----------------------
test_published_release() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [
    {"name": "good-oss-citizen-0.1.4.tar.gz", "state": "uploaded"},
    {"name": "good-oss-citizen-0.1.4.tar.gz.sha256", "state": "uploaded"}
  ]
}
JSON
  local out rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>/dev/null) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "ok" "true" "$(ok_of "$out")" || return 1
  assert_eq "asset count" "2" "$(echo "$out" | jq -r '.assets')" || return 1
  assert_eq "url" "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}" "$(echo "$out" | jq -r '.url')" || return 1
}
run "published release with uploaded assets confirms the publication" test_published_release

# --- Test 2: no release at the tag -------------------------------------------
test_missing_release() {
  set_mode 404
  local out rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"$TAG"* ]] || { echo "    FAIL: reason should name the tag, got: $(reason_of "$out")" >&2; return 1; }
}
run "absent release is a definitive no, not an error" test_missing_release

# --- Test 3: draft release ----------------------------------------------------
test_draft_release() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": true,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [{"name": "pkg.tar.gz", "state": "uploaded"}]
}
JSON
  local out rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"draft"* ]] || { echo "    FAIL: reason should name the draft state, got: $(reason_of "$out")" >&2; return 1; }
}
run "draft release does not confirm a publication" test_draft_release

# --- Test 4: zero assets ------------------------------------------------------
test_no_assets() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": []
}
JSON
  local out rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"no assets"* ]] || { echo "    FAIL: reason should name the empty asset list, got: $(reason_of "$out")" >&2; return 1; }
}
run "release with no assets never passes vacuously" test_no_assets

# --- Test 5: an asset still uploading ----------------------------------------
test_asset_not_uploaded() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [
    {"name": "pkg.tar.gz", "state": "uploaded"},
    {"name": "pkg.tar.gz.sha256", "state": "starter"}
  ]
}
JSON
  local out rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"1 of 2"* ]] || { echo "    FAIL: reason should name the counts, got: $(reason_of "$out")" >&2; return 1; }
}
run "an asset outside the uploaded state blocks confirmation" test_asset_not_uploaded

# --- Test 6: tag mismatch -----------------------------------------------------
test_tag_mismatch() {
  set_body <<JSON
{
  "tag_name": "v0.1.5",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/v0.1.5",
  "assets": [{"name": "pkg.tar.gz", "state": "uploaded"}]
}
JSON
  local out rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$(reason_of "$out")" == *"v0.1.5"* ]] || { echo "    FAIL: reason should name the tag actually returned, got: $(reason_of "$out")" >&2; return 1; }
}
run "a release reporting another tag is refused" test_tag_mismatch

# --- Test 7: auth failure is indeterminate -----------------------------------
test_auth_failure_indeterminate() {
  set_mode auth
  local out stderr rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>"$TMPDIR_TEST/err") || rc=$?
  stderr=$(cat "$TMPDIR_TEST/err")
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
  [[ "$stderr" == *"gh auth status"* ]] || { echo "    FAIL: stderr should carry a recovery hint, got: ${stderr}" >&2; return 1; }
}
run "auth failure is indeterminate, never reported as absent" test_auth_failure_indeterminate

# --- Test 8: unparseable payload ---------------------------------------------
test_unparseable_payload() {
  set_body <<'JSON'
{"unexpected": "shape"}
JSON
  local out rc=0
  out=$(main "$OWNER" "$REPO" "$TAG" 2>/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
}
run "unparseable release payload is indeterminate" test_unparseable_payload

# --- Test 9: argument validation ---------------------------------------------
test_wrong_arg_count() {
  local stderr rc=0
  stderr=$(main "$OWNER" "$REPO" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  [[ "$stderr" == *"usage:"* ]] || { echo "    FAIL: stderr missing usage line, got: ${stderr}" >&2; return 1; }
}
run "wrong argument count exits 2 with usage" test_wrong_arg_count

test_empty_tag() {
  local stderr rc=0
  stderr=$(main "$OWNER" "$REPO" "" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  [[ "$stderr" == *"non-empty"* ]] || { echo "    FAIL: stderr should name the empty argument, got: ${stderr}" >&2; return 1; }
}
run "empty tag argument exits 2" test_empty_tag

# --- Test 10: missing gh ------------------------------------------------------
# Runs in a subshell with the mock removed and PATH emptied, so
# `command -v gh` finds neither the function nor a binary. The guard
# fires before mktemp, so an empty PATH is survivable here.
test_missing_gh() {
  local stderr rc=0
  stderr=$(
    unset -f gh
    # shellcheck disable=SC2123  # emptying the search path is the point: `command -v gh` must find neither the mock function nor a binary
    PATH=""
    main "$OWNER" "$REPO" "$TAG" 2>&1 >/dev/null
  ) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  [[ "$stderr" == *"cli.github.com"* ]] || { echo "    FAIL: stderr should carry an install hint, got: ${stderr}" >&2; return 1; }
}
run "missing gh exits 2 with an install hint" test_missing_gh

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
