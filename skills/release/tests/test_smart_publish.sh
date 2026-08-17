#!/usr/bin/env bash
# Outcome-based tests for smart-publish.sh — the owned publish + credit-signature
# capture that replaces tesslio/patch-version-publish so the confirm gate can
# NAME the failing step (rules/ci-safety.md "Credits Never Block Publishing").
#
# The load-bearing properties:
#   - auto-bump computes the next version REGISTRY-aware (max(registry, manifest)
#     + patch), so a manifest that fell behind the registry does NOT collide the
#     way tessl `--bump patch` would;
#   - the out-of-credits SIGNATURE is read from the publish command's own output
#     (terminal failure line only) and reported only on a non-zero exit;
#   - a non-credit failure reports credit_signature=false (stays red downstream);
#   - commit-back runs ONLY on an auto-bump success, pushes HEAD:<ref-name>, and
#     a rejected push reds the run (no PR fallback);
#   - a registry read failure publishes nothing and reds.
#
# Approach: run the real script against an isolated temporary bare remote + work
# clone (mirrors test_commit_stamp.sh), with a fake `tessl` first on PATH.
# MOCK_REGISTRY drives the versions-API result registry-version.sh reads;
# MOCK_PUBLISH drives the publish result; both are exported through the
# invocation. Real git/jq/python3 stay reachable so the JSON contract, the
# registry read, and the git side effects are exercised for real.
#
# Run: bash skills/release/tests/test_smart_publish.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/smart-publish.sh"
[[ -f "$SCRIPT" ]] || { echo "fatal: smart-publish.sh not found at $SCRIPT" >&2; exit 2; }

TESTTMP="$(mktemp -d)" || { echo "fatal: mktemp -d failed" >&2; exit 2; }
cleanup_tmp() {
  if [[ -n "${TESTTMP:-}" ]]; then
    if ! rm -rf "$TESTTMP"; then
      echo "warning: could not remove temp dir ${TESTTMP} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_tmp EXIT

FAIL_COUNT=0
PASS_COUNT=0
run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# --- fake tessl on PATH (real git/jq/python3 stay reachable) ---
STUBDIR="$TESTTMP/stub"
mkdir -p "$STUBDIR"

# Fake tessl:
#   api v1/tiles/<ws>/<tile>/versions — MOCK_REGISTRY: a version string ->
#       {"data":[{"attributes":{"version":"<v>"}}]}; "empty" -> {"data":[]}
#       (never published); "error" -> non-zero (auth/network failure).
#   plugin publish <path> — MOCK_PUBLISH: ok -> exit 0 (verbatim; smart-publish
#       has already written the target version into the manifest); the *_fail
#       modes print their failure log and exit 1.
cat > "$STUBDIR/tessl" <<'STUB'
#!/usr/bin/env bash
set -uo pipefail
if [[ "${1:-}" == "api" ]]; then
  case "${MOCK_REGISTRY:-}" in
    empty) echo '{"data":[]}' ;;
    error) echo "error: registry unreachable" >&2; exit 1 ;;
    *)     printf '{"data":[{"attributes":{"version":"%s"}}]}\n' "${MOCK_REGISTRY:-}" ;;
  esac
  exit 0
fi
if [[ "${1:-}" == "plugin" && "${2:-}" == "publish" ]]; then
  case "${MOCK_PUBLISH:-}" in
    ok)
      echo "✔ Published"
      exit 0 ;;
    credit_fail)
      # The REAL tessl out-of-credits tail, observed live (fifty-tabs-of-fares
      # 0.16.3): the credit message is followed by a benign `→ URL` link, so the
      # terminal SUBSTANTIVE line is the `✘ …out of credits…` line, NOT the URL.
      echo "✔ Published testws/testplugin@0.1.1"
      echo "✔ Uploaded 3 eval scenarios"
      echo "##[error]Out of credits. Upgrade at https://tessl.io/pricing"
      echo "✘ Your organization has run out of credits. Upgrade your plan or buy more credits to continue."
      echo "→ https://tessl.io/pricing"
      exit 1 ;;
    credit_warning_then_fail)
      # An early credit WARNING (contains 'out of credits'), then a DIFFERENT
      # terminal ##[error]. The credit text is present but is NOT the terminal
      # line — must stay red.
      echo "##[warning]Heads up — you are nearly out of credits"
      echo "✔ Published testws/testplugin@0.1.1"
      echo "##[error]Failed to publish eval scenarios: server error"
      exit 1 ;;
    credit_then_plain_fail)
      # The credit ##[error] is NOT the terminal line — a plain-text failure
      # follows it, so the credit error was not the exit cause. Must stay red.
      echo "##[error]Out of credits"
      echo "Fatal: eval upload connection reset by peer"
      exit 1 ;;
    credit_then_url_fail)
      # Credit mention, then an UNRELATED url-only terminal failure (NOT tessl's
      # pricing link). Only tessl's exact pricing link is benign, so this url
      # stays substantive as the terminal line and the run stays red.
      echo "##[error]Out of credits. Upgrade at https://tessl.io/pricing"
      echo "https://status.example.com/incident/42"
      exit 1 ;;
    other_fail)
      echo "✘ Failed to upload eval scenarios: network error"
      exit 1 ;;
    *) echo "stub tessl: unknown MOCK_PUBLISH='${MOCK_PUBLISH:-}'" >&2; exit 99 ;;
  esac
fi
echo "stub tessl: unsupported invocation: $*" >&2
exit 2
STUB
chmod +x "$STUBDIR/tessl"

export PATH="$STUBDIR:$PATH"

# Isolated bare remote + work clone on `main`, seeded with a plugin manifest at
# the given version. Echoes the sandbox root ($root/work, $root/remote.git).
_sandbox() {
  local version="$1" root
  root="$(mktemp -d "$TESTTMP/sbx.XXXXXX")" || { echo "fatal: sandbox mktemp -d failed" >&2; return 1; }
  git init --bare -q -b main "$root/remote.git"
  git init -q -b main "$root/work"
  git -C "$root/work" remote add origin "$root/remote.git"
  git -C "$root/work" config user.name "seed"
  git -C "$root/work" config user.email "seed@example.com"
  mkdir -p "$root/work/.tessl-plugin"
  printf '{\n  "name": "testws/testplugin",\n  "version": "%s",\n  "description": "x"\n}\n' "$version" \
    > "$root/work/.tessl-plugin/plugin.json"
  git -C "$root/work" add .tessl-plugin/plugin.json
  git -C "$root/work" commit -q -m "seed"
  git -C "$root/work" push -q -u origin main
  echo "$root"
}

# Advance the remote's main from a throwaway clone so the work clone's next push
# is non-fast-forward (simulates a branch-protection-blocked direct push).
_advance_remote_main() {
  local root="$1" scratch="$1/scratch"
  git clone -q "$root/remote.git" "$scratch"
  git -C "$scratch" config user.name "other"
  git -C "$scratch" config user.email "other@example.com"
  printf 'diverge\n' > "$scratch/other.txt"
  git -C "$scratch" add other.txt
  git -C "$scratch" commit -q -m "remote advanced"
  git -C "$scratch" push -q origin main
}

_remote_count()   { git -C "$1/remote.git" rev-list --count main; }
_remote_msg()     { git -C "$1/remote.git" log -1 --format=%B main; }
_remote_version() { git -C "$1/remote.git" show main:.tessl-plugin/plugin.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])'; }
_remote_has_bump_branch() { git -C "$1/remote.git" for-each-ref --format='%(refname)' 'refs/heads/tessl-bump-*' | grep -q .; }

# Run smart-publish.sh in $work with the given MOCK_* + args; capture JSON stdout.
_run() {
  local root="$1" registry="$2" pub="$3"; shift 3
  ( cd "$root/work" && MOCK_REGISTRY="$registry" MOCK_PUBLISH="$pub" bash "$SCRIPT" "$@" )
}

# --- test bodies ---

t_autobump_success_commits_and_pushes_bump() {
  # manifest 0.1.0, registry 0.1.0 -> target 0.1.1 (registry + patch).
  local root; root="$(_sandbox 0.1.0)"
  local out; out="$(_run "$root" 0.1.0 ok auto-bump . main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="success" and d["version"]=="0.1.1" and d["credit_signature"] is False and d["first_publish"] is False' \
    || { echo "    FAIL: unexpected JSON: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "2" ]] || { echo "    FAIL: expected the bump commit on the remote" >&2; return 1; }
  [[ "$(_remote_msg "$root")" == *"Bump testws/testplugin to 0.1.1"* ]] || { echo "    FAIL: bump commit message wrong: $(_remote_msg "$root")" >&2; return 1; }
  [[ "$(_remote_msg "$root")" == *"[skip ci]"* ]] || { echo "    FAIL: bump commit missing [skip ci]" >&2; return 1; }
  [[ "$(_remote_version "$root")" == "0.1.1" ]] || { echo "    FAIL: remote manifest not bumped to 0.1.1" >&2; return 1; }
}

t_stale_manifest_bumps_from_registry_no_collision() {
  # THE REGRESSION: manifest fell behind (0.1.0) while the registry advanced to
  # 0.1.5 (a credit-outage run skipped the commit-back). tessl `--bump patch`
  # would try 0.1.1 and collide; registry-aware compute targets 0.1.6.
  local root; root="$(_sandbox 0.1.0)"
  local out; out="$(_run "$root" 0.1.5 ok auto-bump . main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="success" and d["version"]=="0.1.6"' \
    || { echo "    FAIL: a stale manifest must bump from the registry (0.1.5 -> 0.1.6), not the manifest (0.1.1): $out" >&2; return 1; }
  [[ "$(_remote_version "$root")" == "0.1.6" ]] || { echo "    FAIL: remote manifest not set to 0.1.6" >&2; return 1; }
  [[ "$(_remote_msg "$root")" == *"Bump testws/testplugin to 0.1.6"* ]] || { echo "    FAIL: bump commit message wrong: $(_remote_msg "$root")" >&2; return 1; }
}

t_manifest_ahead_publishes_manifest() {
  # A human bumped the manifest to 0.2.0 (minor) while the registry is at 0.1.5.
  # The manifest is ahead -> publish it as-is (0.2.0), do not registry-patch.
  local root; root="$(_sandbox 0.2.0)"
  local out; out="$(_run "$root" 0.1.5 ok auto-bump . main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="success" and d["version"]=="0.2.0"' \
    || { echo "    FAIL: a manifest ahead of the registry must publish the manifest version (0.2.0): $out" >&2; return 1; }
}

t_credit_fail_reports_signature_no_commit() {
  local root; root="$(_sandbox 0.1.0)"
  local before; before="$(_remote_count "$root")"
  local out rc
  out="$(_run "$root" 0.1.0 credit_fail auto-bump . main branch)"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit on a failed publish" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="failure" and d["credit_signature"] is True and d["version"] is None' \
    || { echo "    FAIL: unexpected JSON: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "$before" ]] || { echo "    FAIL: a commit was pushed on a failed publish" >&2; return 1; }
}

t_credit_warning_then_other_fail_stays_red() {
  local root; root="$(_sandbox 0.1.0)"
  local out rc
  out="$(_run "$root" 0.1.0 credit_warning_then_fail auto-bump . main branch)"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="failure" and d["credit_signature"] is False' \
    || { echo "    FAIL: an early credit warning + a different terminal failure must report credit_signature=false: $out" >&2; return 1; }
}

t_credit_error_then_plain_terminal_fail_stays_red() {
  local root; root="$(_sandbox 0.1.0)"
  local out rc
  out="$(_run "$root" 0.1.0 credit_then_plain_fail auto-bump . main branch)"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="failure" and d["credit_signature"] is False' \
    || { echo "    FAIL: a credit error followed by a plain-text terminal failure must report credit_signature=false: $out" >&2; return 1; }
}

t_credit_then_unrelated_url_fail_stays_red() {
  local root; root="$(_sandbox 0.1.0)"
  local out rc
  out="$(_run "$root" 0.1.0 credit_then_url_fail auto-bump . main branch)"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="failure" and d["credit_signature"] is False' \
    || { echo "    FAIL: a credit mention + an unrelated url-only terminal failure must report credit_signature=false: $out" >&2; return 1; }
}

t_other_fail_no_signature_no_commit() {
  local root; root="$(_sandbox 0.1.0)"
  local before; before="$(_remote_count "$root")"
  local out rc
  out="$(_run "$root" 0.1.0 other_fail auto-bump . main branch)"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: expected non-zero exit" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="failure" and d["credit_signature"] is False' \
    || { echo "    FAIL: a non-credit failure must report credit_signature=false: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "$before" ]] || { echo "    FAIL: a commit was pushed on a failed publish" >&2; return 1; }
}

t_asis_success_does_not_commit_back() {
  # as-is publishes the manifest version verbatim, never reading the registry.
  local root; root="$(_sandbox 0.2.0)"
  local before; before="$(_remote_count "$root")"
  local out; out="$(_run "$root" error ok as-is . main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="success" and d["version"]=="0.2.0"' \
    || { echo "    FAIL: unexpected JSON: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "$before" ]] || { echo "    FAIL: as-is mode must not commit back" >&2; return 1; }
}

t_first_publish_sets_flag_no_bump() {
  # registry empty (never published) -> target = manifest, first_publish true;
  # manifest unchanged -> nothing to commit back.
  local root; root="$(_sandbox 0.1.0)"
  local before; before="$(_remote_count "$root")"
  local out; out="$(_run "$root" empty ok auto-bump . main branch)" \
    || { echo "    FAIL: script exited non-zero" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="success" and d["first_publish"] is True and d["version"]=="0.1.0"' \
    || { echo "    FAIL: first publish should set first_publish=true, version 0.1.0: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "$before" ]] || { echo "    FAIL: first publish should not commit a bump" >&2; return 1; }
}

t_registry_read_failure_publishes_nothing() {
  local root; root="$(_sandbox 0.1.0)"
  local before; before="$(_remote_count "$root")"
  local out rc
  out="$(_run "$root" error ok auto-bump . main branch)"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: a registry read failure must exit non-zero" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="failure" and d["credit_signature"] is False and d["version"] is None' \
    || { echo "    FAIL: unexpected JSON: $out" >&2; return 1; }
  [[ "$(_remote_count "$root")" == "$before" ]] || { echo "    FAIL: nothing should be committed on a pre-publish failure" >&2; return 1; }
}

t_push_blocked_reds_the_run() {
  # manifest 0.1.0, registry 0.1.0 -> target 0.1.1; publish ok; commit-back push
  # blocked (non-ff) -> red, no PR fallback.
  local root; root="$(_sandbox 0.1.0)"
  _advance_remote_main "$root"
  local out rc
  out="$(_run "$root" 0.1.0 ok auto-bump . main branch)"; rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: a rejected bump-push must red the run" >&2; return 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["outcome"]=="failure" and d["credit_signature"] is False and d["version"]=="0.1.1"' \
    || { echo "    FAIL: blocked push should emit outcome=failure, credit_signature=false, version 0.1.1: $out" >&2; return 1; }
  if _remote_has_bump_branch "$root"; then
    echo "    FAIL: a fallback bump branch was pushed — there must be no PR fallback" >&2; return 1
  fi
}

t_wrong_arity_is_usage_error() {
  local root; root="$(_sandbox 0.1.0)"
  local err rc
  err="$( ( cd "$root/work" && bash "$SCRIPT" auto-bump . ) 2>&1 >/dev/null )"; rc=$?
  [[ "$rc" -eq 2 ]] || { echo "    FAIL: expected exit 2 on wrong arity, got $rc" >&2; return 1; }
  [[ "$err" == *"usage:"* ]] || { echo "    FAIL: missing usage message: $err" >&2; return 1; }
}

main() {
  echo "== smart-publish.sh tests =="
  run "auto-bump success commits + pushes the bump [skip ci]"    t_autobump_success_commits_and_pushes_bump
  run "stale manifest bumps from the registry (no collision)"    t_stale_manifest_bumps_from_registry_no_collision
  run "manifest ahead of registry publishes the manifest"        t_manifest_ahead_publishes_manifest
  run "credit failure reports credit_signature, no commit"       t_credit_fail_reports_signature_no_commit
  run "early credit warning + other terminal failure -> red"     t_credit_warning_then_other_fail_stays_red
  run "credit error + plain-text terminal failure -> red"        t_credit_error_then_plain_terminal_fail_stays_red
  run "credit + unrelated url-only terminal failure -> red"      t_credit_then_unrelated_url_fail_stays_red
  run "non-credit failure reports no signature, no commit"       t_other_fail_no_signature_no_commit
  run "as-is success does not commit back"                       t_asis_success_does_not_commit_back
  run "first publish (empty registry) sets flag, no bump"        t_first_publish_sets_flag_no_bump
  run "registry read failure publishes nothing"                  t_registry_read_failure_publishes_nothing
  run "a blocked bump-push reds the run (no PR fallback)"        t_push_blocked_reds_the_run
  run "wrong arity is a usage error (exit 2)"                    t_wrong_arity_is_usage_error
  echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
  [[ "$FAIL_COUNT" -eq 0 ]]
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
