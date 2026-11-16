#!/usr/bin/env bash
# Outcome tests for license.sh --write <year> <holder>: it creates the exact
# MIT text for the supplied year and holder and stages only LICENSE, never
# touches a tracked, untracked or locally deleted LICENSE, rejects invalid
# arguments without writing, tells a broken repository from no repository,
# leaves no LICENSE after a failed write, and reports a staging failure. Also
# checks that coding-policy carries the MIT license the operator approved.
# Each case builds a throwaway repo; order-independent.
#
# Run: bash tests/onboard-repo/tests/test_license.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/skills/onboard-repo/license.sh"
WRITTEN='{"source":"LICENSE","written":true}'
REFUSED='{"source":"LICENSE","written":false}'

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
setup_fail() { echo "fatal: test setup failed: $1" >&2; exit 2; }

# A repo with one committed file.
mkrepo() {
  if ! { git init -q "$1" && ( cd "$1" && git config user.name "Test Owner" && git config user.email owner@example.com \
         && printf 'x\n' > a.txt && git add a.txt && git commit -q -m init ); }; then
    setup_fail "repo $1"
  fi
}

# The fixed MIT text, independent of license.sh. <year> <holder>
expected_mit() {
  cat <<EOF
MIT License

Copyright (c) $1 $2

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF
}

main() {
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: license.sh not found at $SCRIPT" >&2; exit 2; }
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/test-license.XXXXXX")" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  FAIL=0; PASS=0
  local d out rc before after args

  # 1. a tracked LICENSE is kept: --write refuses without touching it.
  d="$TMP/1"; mkrepo "$d"
  { printf 'Apache License\n' > "$d/LICENSE" && git -C "$d" add LICENSE; } || setup_fail "case 1 LICENSE"
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 "Test Owner" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 1 && "$out" == "$REFUSED" ]] && after=$(cat "$d/LICENSE") && [[ "$after" == "Apache License" ]]; then pass
  else fail "write over a tracked LICENSE: rc=$rc out=$out"; fi

  # 2. an untracked LICENSE is kept and stays untracked.
  d="$TMP/2"; mkrepo "$d"
  printf 'Unapproved draft\n' > "$d/LICENSE" || setup_fail "case 2 LICENSE"
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 "Test Owner" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 1 && "$out" == "$REFUSED" ]] && after=$(cat "$d/LICENSE") \
     && [[ "$after" == "Unapproved draft" && -z "$(git -C "$d" ls-files -- LICENSE)" ]]; then pass
  else fail "write over an untracked LICENSE: rc=$rc out=$out"; fi

  # 3. a tracked LICENSE deleted from the working tree is not replaced.
  d="$TMP/3"; mkrepo "$d"
  { printf 'Apache License\n' > "$d/LICENSE" && git -C "$d" add LICENSE && git -C "$d" commit -q -m license \
    && rm "$d/LICENSE"; } || setup_fail "case 3 LICENSE"
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 "Test Owner" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 1 && "$out" == "$REFUSED" && ! -e "$d/LICENSE" ]] \
     && [[ "$(git -C "$d" show HEAD:LICENSE)" == "Apache License" && -z "$(git -C "$d" diff --cached --name-only)" ]]; then pass
  else fail "write over a locally deleted tracked LICENSE: rc=$rc out=$out"; fi

  # 4. with no LICENSE, --write creates the exact MIT text for the supplied
  #    year and holder and stages only LICENSE, leaving other changes unstaged.
  #    It runs from a subdirectory and writes at the root.
  d="$TMP/4"; mkrepo "$d"
  { printf 'edit\n' >> "$d/a.txt" && printf 'new\n' > "$d/b.txt" && mkdir "$d/sub"; } || setup_fail "case 4 changes"
  out="$(cd "$d/sub" && bash "$SCRIPT" --write 2019 "Jane Q. Holder" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$out" == "$WRITTEN" ]] && expected_mit 2019 "Jane Q. Holder" | cmp -s - "$d/LICENSE" \
     && [[ "$(git -C "$d" diff --cached --name-only)" == "LICENSE" ]]; then pass
  else fail "write and stage MIT: rc=$rc out=$out"; fi

  # 4b. a second --write refuses and keeps the first LICENSE.
  if before=$(cat "$d/LICENSE"); then
    out="$(cd "$d" && bash "$SCRIPT" --write 2024 "Someone Else" 2>>"$TMP/stderr.log")"; rc=$?
    if [[ $rc -eq 1 && "$out" == "$REFUSED" ]] && after=$(cat "$d/LICENSE") && [[ "$after" == "$before" ]]; then pass
    else fail "second write: rc=$rc out=$out"; fi
  else fail "case 4b snapshot"; fi

  # 5. invalid arguments are a usage error and write nothing.
  d="$TMP/5"; mkrepo "$d"
  for args in "" "--write" "--write 2019" "--bogus 2019 Owner" "--write 19 Owner" "--write 20x9 Owner" \
              "--write 2019 Owner extra"; do
    # shellcheck disable=SC2086 # word-splitting the fixture argument list is the point
    out="$(cd "$d" && bash "$SCRIPT" $args 2>>"$TMP/stderr.log")"; rc=$?
    if [[ $rc -eq 2 && -z "$out" && ! -e "$d/LICENSE" ]]; then pass; else fail "usage '$args': rc=$rc out=$out"; fi
  done
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 "" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" && ! -e "$d/LICENSE" ]]; then pass; else fail "empty holder: rc=$rc out=$out"; fi
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 $'Owner\nInjected line' 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" && ! -e "$d/LICENSE" ]]; then pass; else fail "multi-line holder: rc=$rc out=$out"; fi

  # 6. outside a git worktree it stops and writes nothing.
  d="$TMP/6"; mkdir "$d" || setup_fail "case 6 dir"
  out="$(cd "$d" && GIT_CEILING_DIRECTORIES="$TMP" bash "$SCRIPT" --write 2019 "Test Owner" 2>"$TMP/6.err")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" && ! -e "$d/LICENSE" ]] && grep -qF "not inside a git worktree" "$TMP/6.err"; then pass
  else fail "outside a worktree: rc=$rc out=$out"; fi

  # 7. a staging failure (a held index lock) exits 2 and says how to recover.
  d="$TMP/7"; mkrepo "$d"
  : > "$d/.git/index.lock" || setup_fail "case 7 lock"
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 "Test Owner" 2>"$TMP/7.err")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" ]] && grep -qF "run 'git add LICENSE'" "$TMP/7.err"; then pass
  else fail "staging failure: rc=$rc out=$out"; fi

  # 6b. corrupt metadata is a git failure, not "outside a worktree": --write
  #     reports git's own diagnostic and writes nothing. A bad config line
  #     and a gitfile pointing nowhere both fail rev-parse like a plain
  #     directory does.
  d="$TMP/6b"; mkrepo "$d"
  printf '[core\nbroken\n' > "$d/.git/config" || setup_fail "case 6b config"
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 "Test Owner" 2>"$TMP/6b.err")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" && ! -e "$d/LICENSE" ]] && grep -qF "bad config line" "$TMP/6b.err" \
     && ! grep -qF "not inside a git worktree" "$TMP/6b.err"; then pass
  else fail "corrupt git config: rc=$rc out=$out err=$(cat "$TMP/6b.err")"; fi
  d="$TMP/6c"; mkdir "$d" || setup_fail "case 6c dir"
  printf 'gitdir: %s\n' "$TMP/6c-missing" > "$d/.git" || setup_fail "case 6c gitfile"
  out="$(cd "$d" && GIT_CEILING_DIRECTORIES="$TMP" bash "$SCRIPT" --write 2019 "Test Owner" 2>"$TMP/6c.err")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" && ! -e "$d/LICENSE" ]] && grep -qF "fatal: " "$TMP/6c.err" \
     && ! grep -qF "not inside a git worktree" "$TMP/6c.err"; then pass
  else fail "dangling gitfile: rc=$rc out=$out err=$(cat "$TMP/6c.err")"; fi

  # 7b. a write failing after LICENSE is created (quota, I/O) exits 2 and
  #     leaves no LICENSE, so a rerun succeeds. A `cat` on PATH that writes
  #     part of the text and fails stands in for the failing write.
  d="$TMP/7b"; mkrepo "$d"
  { mkdir "$TMP/fault" && printf '#!/bin/sh\nprintf "MIT Lic"\nexit 1\n' > "$TMP/fault/cat" && chmod +x "$TMP/fault/cat"; } \
    || setup_fail "case 7b fault cat"
  out="$(cd "$d" && PATH="$TMP/fault:$PATH" bash "$SCRIPT" --write 2019 "Test Owner" 2>"$TMP/7b.err")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" && ! -e "$d/LICENSE" && -z "$(git -C "$d" diff --cached --name-only)" ]]; then pass
  else fail "failed write: rc=$rc out=$out err=$(cat "$TMP/7b.err")"; fi
  out="$(cd "$d" && bash "$SCRIPT" --write 2019 "Test Owner" 2>>"$TMP/stderr.log")"; rc=$?
  if [[ $rc -eq 0 && "$out" == "$WRITTEN" ]] && expected_mit 2019 "Test Owner" | cmp -s - "$d/LICENSE"; then pass
  else fail "rerun after a failed write: rc=$rc out=$out"; fi

  # 7c. when the partial LICENSE cannot be removed either, it exits 2 and
  #     says to delete LICENSE by hand. An `rm` on PATH that fails on LICENSE
  #     stands in for the failing removal.
  d="$TMP/7c"; mkrepo "$d"
  { mkdir "$TMP/fault-rm" && cp "$TMP/fault/cat" "$TMP/fault-rm/cat" \
    && printf '#!/bin/sh\ncase "$*" in *LICENSE*) exit 1 ;; esac\nexec /bin/rm "$@"\n' > "$TMP/fault-rm/rm" \
    && chmod +x "$TMP/fault-rm/rm"; } || setup_fail "case 7c fault rm"
  out="$(cd "$d" && PATH="$TMP/fault-rm:$PATH" bash "$SCRIPT" --write 2019 "Test Owner" 2>"$TMP/7c.err")"; rc=$?
  if [[ $rc -eq 2 && -z "$out" ]] && grep -qF "delete LICENSE by hand" "$TMP/7c.err"; then pass
  else fail "failed cleanup: rc=$rc out=$out err=$(cat "$TMP/7c.err")"; fi

  # 8. coding-policy itself carries the MIT license the operator approved.
  if expected_mit 2026 "Baruch Sadogursky" | cmp -s - "$REPO_ROOT/LICENSE"; then pass
  else fail "coding-policy's LICENSE is not MIT, Copyright (c) 2026 Baruch Sadogursky"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
