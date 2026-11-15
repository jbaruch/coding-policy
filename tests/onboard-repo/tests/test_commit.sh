#!/usr/bin/env bash
# Outcome tests for commit.sh and LICENSE: a LICENSE that license.sh --write
# staged lands in the onboarding commit, while an untracked LICENSE draft and a
# tracked LICENSE with uncommitted edits stay out of it. Each case builds a
# throwaway repo.
#
# Run: bash tests/onboard-repo/tests/test_commit.sh
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../skills/onboard-repo" && pwd)/commit.sh"

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
setup_fail() { echo "fatal: test setup failed: $1" >&2; exit 2; }

# A repo on main with one commit, switched to the onboarding branch with the
# three reviewer files in place. $2: "tracked" commits a LICENSE on main first.
mkrepo() {
  if ! { git init -q -b main "$1" && ( cd "$1" && git config user.name t && git config user.email t@example.com \
         && printf 'x\n' > a.txt && git add a.txt && git commit -q -m init ); }; then
    setup_fail "repo $1"
  fi
  if [[ "${2:-}" == tracked ]]; then
    ( cd "$1" && printf 'Original license\n' > LICENSE && git add LICENSE && git commit -q -m license ) || setup_fail "tracked LICENSE in $1"
  fi
  ( cd "$1" && git checkout -q -b feat/add-coding-policy-review && mkdir -p .github/workflows \
    && printf '# marker\n' > .github/fleet-review-enabled && printf 'on: pull_request\n' > .github/workflows/review-trigger.yml \
    && printf 'FLEET_DISPATCH_TOKEN=\n' > .env.example ) || setup_fail "reviewer files in $1"
}

main() {
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: commit.sh not found at $SCRIPT" >&2; exit 2; }
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/test-commit.XXXXXX")" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  FAIL=0; PASS=0
  local d rc committed

  # 1. a staged LICENSE, as license.sh --write leaves it, is committed with the
  #    reviewer files.
  d="$TMP/1"; mkrepo "$d"
  ( cd "$d" && printf 'MIT License\n' > LICENSE && git add LICENSE ) || setup_fail "case 1 LICENSE"
  ( cd "$d" && bash "$SCRIPT" >/dev/null 2>>"$TMP/stderr.log" ); rc=$?
  if [[ $rc -eq 0 ]] && committed=$(cd "$d" && git show HEAD:LICENSE) && [[ "$committed" == "MIT License" ]]; then pass
  else fail "staged LICENSE not committed (rc=$rc)"; fi

  # 1b. an untracked LICENSE draft is left out of the commit.
  d="$TMP/1b"; mkrepo "$d"
  printf 'Draft license\n' > "$d/LICENSE" || setup_fail "case 1b LICENSE"
  ( cd "$d" && bash "$SCRIPT" >/dev/null 2>>"$TMP/stderr.log" ); rc=$?
  if [[ $rc -eq 0 ]] && [[ -z "$(cd "$d" && git ls-files -- LICENSE)" ]] && [[ -e "$d/LICENSE" ]]; then pass
  else fail "untracked LICENSE draft was committed (rc=$rc)"; fi

  # 2. a tracked LICENSE with uncommitted edits is left out of the commit.
  d="$TMP/2"; mkrepo "$d" tracked
  printf 'Someone else\x27s edit\n' > "$d/LICENSE" || setup_fail "case 2 edit"
  ( cd "$d" && bash "$SCRIPT" >/dev/null 2>>"$TMP/stderr.log" ); rc=$?
  if [[ $rc -eq 0 ]] && committed=$(cd "$d" && git show HEAD:LICENSE) && [[ "$committed" == "Original license" ]] \
     && [[ -n "$(cd "$d" && git status --porcelain -- LICENSE)" ]]; then pass
  else fail "tracked LICENSE edits were swept into the commit (rc=$rc)"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
