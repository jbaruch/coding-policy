#!/usr/bin/env bash
# Write an MIT LICENSE at the repository root for a confirmed year and holder,
# and stage it.
#
# The caller (onboard-repo Step 5) owns every licensing judgment: that the
# operator owns the repository, that it declares no license, and that the
# operator confirmed MIT with this year and holder. This script checks none of
# that. It never rewrites or removes an existing LICENSE: one whose bytes match
# the requested MIT text is reused and staged, so rerunning the same command
# converges; any other LICENSE in the working tree or the index is refused.
#
# Usage: license.sh --write <year> <holder>
#   <year>     four-digit copyright year
#   <holder>   copyright holder, one non-empty line
# Out:   one JSON object on stdout:
#          {"source":"LICENSE","written":true|false}
# Exit:  0 when LICENSE holds the requested MIT text and is staged: written
#        true when this run created it, false when it was already there;
#        1 when LICENSE exists with other content, is not a regular file, or
#        is tracked but deleted (nothing changes, written false); 2 on a
#        usage, environment or I/O error, where a failed write leaves no
#        LICENSE. Diagnostics go to stderr.
set -euo pipefail

usage() { echo "usage: license.sh --write <year> <holder> — year is four digits, holder one non-empty line" >&2; exit 2; }

# Fixed MIT text. Two placeholders: the year and the holder.
mit_text() { # <year> <holder>
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

refuse() { # <reason>
  echo "error: refusing --write — $1; keep it, or change it by hand and stage it before the commit step" >&2
  printf '{"source":"LICENSE","written":false}\n'
  exit 1
}

main() {
  (( $# == 3 )) && [[ "$1" == "--write" && "$2" =~ ^[0-9]{4}$ && -n "$3" && "$3" != *[$'\n\r']* ]] || usage
  local year="$2" holder="$3" root tracked expected compared=0 written=false
  # git prints its own diagnostic to stderr above this one.
  if ! root=$(git rev-parse --show-toplevel); then
    echo "error: cannot determine the Git worktree root — inspect the Git error above with 'git status', then rerun from the consumer worktree" >&2
    exit 2
  fi
  cd "$root"
  if [[ -L LICENSE ]]; then refuse "LICENSE is a symlink"; fi
  if [[ -e LICENSE ]]; then
    if [[ ! -f LICENSE ]]; then refuse "LICENSE exists and is not a regular file"; fi
    if ! expected=$(mit_text "$year" "$holder"); then
      echo "error: could not render the MIT text — check the system 'cat' works, then rerun" >&2
      exit 2
    fi
    # The substitution strips the text's final newline; the here-string
    # restores it, so only identical bytes compare equal.
    cmp -s - LICENSE <<< "$expected" || compared=$?
    case $compared in
      0) ;;
      1) refuse "LICENSE differs from the MIT text for $year $holder" ;;
      *) echo "error: could not compare LICENSE with the MIT text — check LICENSE is readable, then rerun" >&2; exit 2 ;;
    esac
  else
    if ! tracked=$(git ls-files -- LICENSE); then
      echo "error: could not read the git index — check 'git status' and re-run" >&2
      exit 2
    fi
    # A tracked LICENSE deleted from the working tree is still the repo's license.
    if [[ -n "$tracked" ]]; then refuse "LICENSE is tracked by git but missing from the working tree; restore it with 'git restore LICENSE'"; fi
    # noclobber creates LICENSE exclusively, so a file appearing since the check
    # above is never overwritten.
    if ! ( set -o noclobber; : > LICENSE ); then
      echo "error: could not create LICENSE — check the repo root is writable and LICENSE is absent, then re-run" >&2
      exit 2
    fi
    # This run created LICENSE, so a failed write (quota, I/O) removes it rather
    # than leave a partial file the next run would refuse.
    if ! mit_text "$year" "$holder" >> LICENSE; then
      if ! rm -f -- LICENSE; then
        echo "error: could not write LICENSE, nor remove the partial file — delete LICENSE by hand, check free space and quota, then re-run" >&2
        exit 2
      fi
      echo "error: could not write LICENSE; removed the partial file — check free space and quota, then re-run" >&2
      exit 2
    fi
    written=true
  fi
  # Staged here, so the onboarding commit takes this LICENSE.
  if ! git add -- LICENSE; then
    echo "error: LICENSE holds the MIT text but could not be staged — fix the cause git printed above, then rerun this same license.sh command; when a .gitignore pattern matches LICENSE, run 'git add -f LICENSE' instead, then run the commit step" >&2
    exit 2
  fi
  printf '{"source":"LICENSE","written":%s}\n' "$written"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
