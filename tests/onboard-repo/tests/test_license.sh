#!/usr/bin/env bash
# Outcome tests for license.sh: it keeps an existing license file or a declared
# license, reports a README License heading for the caller to read, lists
# outside commit authors, and with --write adds and stages MIT only when no
# license file or declaration exists. Each case builds a throwaway repo;
# order-independent.
#
# Run: bash tests/onboard-repo/tests/test_license.sh
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../skills/onboard-repo" && pwd)/license.sh"

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
setup_fail() { echo "fatal: test setup failed: $1" >&2; exit 2; }

# A repo whose operator is "Test Owner <owner@example.com>", with one root
# commit dated 2019 by the operator.
mkrepo() {
  if ! { git init -q "$1" && ( cd "$1" && git config user.name "Test Owner" && git config user.email owner@example.com \
         && printf 'x\n' > a.txt && git add a.txt \
         && GIT_AUTHOR_DATE="2019-05-01T00:00:00Z" GIT_COMMITTER_DATE="2019-05-01T00:00:00Z" git commit -q -m init ); }; then
    setup_fail "repo $1"
  fi
}
# commit_as <repo> <name> <email> <file>
commit_as() {
  ( cd "$1" && printf '%s\n' "$4" > "$4" && git add "$4" && git -c user.name="$2" -c user.email="$3" commit -q -m "$4" ) \
    || setup_fail "commit in $1"
}
field() { python3 -I -c 'import json,sys; d=json.load(sys.stdin); v=d[sys.argv[1]]; print(json.dumps(v) if isinstance(v,(list,bool)) or v is None else v)' "$1"; }

main() {
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: license.sh not found at $SCRIPT" >&2; exit 2; }
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/test-license.XXXXXX")" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  FAIL=0; PASS=0
  local d out rc before after head_sha

  # 1. an existing LICENSE file is kept, and --write refuses without touching it.
  d="$TMP/1"; mkrepo "$d"
  printf 'Apache License\n' > "$d/LICENSE" || setup_fail "case 1 LICENSE"
  out="$(cd "$d" && bash "$SCRIPT" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$(field license <<<"$out")" == "file" && "$(field source <<<"$out")" == "LICENSE" ]]; then pass
  else fail "existing file: rc=$rc out=$out"; fi
  out="$(cd "$d" && bash "$SCRIPT" --write 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 1 ]] && after=$(cat "$d/LICENSE") && [[ "$after" == "Apache License" ]]; then pass
  else fail "write over existing file: rc=$rc"; fi

  # 2. a license declared in package.json counts as a license.
  d="$TMP/2"; mkrepo "$d"
  printf '{"name":"x","license":"Apache-2.0"}\n' > "$d/package.json" || setup_fail "case 2 package.json"
  out="$(cd "$d" && bash "$SCRIPT" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$(field license <<<"$out")" == "declared" && "$(field source <<<"$out")" == "package.json" ]]; then pass
  else fail "declared in package.json: rc=$rc out=$out"; fi

  # 2b. a proprietary license declared in pyproject.toml is kept: --write refuses
  #     and writes no LICENSE (the fifty-tabs-of-fares case in #800).
  d="$TMP/2b"; mkrepo "$d"
  printf '[project]\nname = "x"\nlicense = "LicenseRef-Proprietary"\n' > "$d/pyproject.toml" || setup_fail "case 2b pyproject"
  out="$(cd "$d" && bash "$SCRIPT" --write 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 1 && "$(field license <<<"$out")" == "declared" && "$(field source <<<"$out")" == "pyproject.toml" && ! -e "$d/LICENSE" ]]; then pass
  else fail "proprietary pyproject: rc=$rc out=$out"; fi

  # 3. a README License heading is reported for the caller to read, and
  #    --write may still add MIT when the caller finds it a placeholder.
  d="$TMP/3"; mkrepo "$d"
  printf '# x\n\n## License\n\nTBD.\n' > "$d/README.md" || setup_fail "case 3 README"
  out="$(cd "$d" && bash "$SCRIPT" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$(field license <<<"$out")" == "readme" && "$(field source <<<"$out")" == "README.md" ]]; then pass
  else fail "README heading: rc=$rc out=$out"; fi
  out="$(cd "$d" && bash "$SCRIPT" --write 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$(field written <<<"$out")" == "true" ]] && grep -qxF 'MIT License' "$d/LICENSE"; then pass
  else fail "write under a placeholder README heading: rc=$rc out=$out"; fi

  # 4. operator-only history with a bot commit: no outside authors, and --write
  #    adds MIT for the operator dated by the first commit.
  d="$TMP/4"; mkrepo "$d"
  commit_as "$d" "dependabot[bot]" "49699333+dependabot[bot]@users.noreply.github.com" b.txt
  out="$(cd "$d" && bash "$SCRIPT" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$(field license <<<"$out")" == "none" && "$(field outside_authors <<<"$out")" == "[]" ]]; then pass
  else fail "operator-only report: rc=$rc out=$out"; fi
  out="$(cd "$d" && bash "$SCRIPT" --write 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$(field written <<<"$out")" == "true" ]] \
     && grep -qxF 'MIT License' "$d/LICENSE" && grep -qxF 'Copyright (c) 2019 Test Owner' "$d/LICENSE" \
     && [[ "$(cd "$d" && git diff --cached --name-only)" == "LICENSE" ]]; then pass
  else fail "write and stage MIT: rc=$rc out=$out"; fi
  if before=$(cat "$d/LICENSE"); then
    out="$(cd "$d" && bash "$SCRIPT" --write 2>>"$TMP/stderr.log")"; rc=$?
    if [[ $rc -eq 1 ]] && after=$(cat "$d/LICENSE") && [[ "$after" == "$before" ]]; then pass
    else fail "second write: rc=$rc"; fi
  else fail "case 4 snapshot"; fi

  # 5. an outside author is listed for the operator's judgment.
  d="$TMP/5"; mkrepo "$d"
  commit_as "$d" "Alice" "alice@example.com" c.txt
  out="$(cd "$d" && bash "$SCRIPT" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$(field outside_authors <<<"$out")" == '["Alice <alice@example.com>"]' ]]; then pass
  else fail "outside author: rc=$rc out=$out"; fi

  # 5b. a repo with no commits yet dates the license this year.
  d="$TMP/5b"
  if ! { git init -q "$d" && ( cd "$d" && git config user.name "Test Owner" && git config user.email owner@example.com ); }; then
    setup_fail "case 5b repo"
  fi
  out="$(cd "$d" && bash "$SCRIPT" --write 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 ]] && grep -qxF "Copyright (c) $(date +%Y) Test Owner" "$d/LICENSE"; then pass
  else fail "unborn HEAD: rc=$rc out=$out"; fi

  # 5c. an unreadable history stops before any LICENSE is written.
  d="$TMP/5c"; mkrepo "$d"
  if ! { head_sha="$(cd "$d" && git rev-parse HEAD)" && rm "$d/.git/objects/${head_sha:0:2}/${head_sha:2}"; }; then
    setup_fail "case 5c corrupt"
  fi
  out="$(cd "$d" && bash "$SCRIPT" --write 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 2 && ! -e "$d/LICENSE" ]]; then pass; else fail "corrupt history: rc=$rc out=$out"; fi

  # 6. an unknown flag is a usage error.
  d="$TMP/6"; mkrepo "$d"
  out="$(cd "$d" && bash "$SCRIPT" --bogus 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" ]]; then pass; else fail "usage: rc=$rc"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
