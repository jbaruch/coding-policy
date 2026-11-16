#!/usr/bin/env bash
# Write an MIT LICENSE at the repository root for a confirmed year and holder,
# and stage it.
#
# The caller (onboard-repo Step 5) owns every licensing judgment: that the
# operator owns the repository, that it declares no license, and that the
# operator confirmed MIT with this year and holder. This script checks none of
# that. It only refuses to touch a LICENSE that already exists in the working
# tree or the index.
#
# Usage: license.sh --write <year> <holder>
#   <year>     four-digit copyright year
#   <holder>   copyright holder, one non-empty line
# Out:   one JSON object on stdout:
#          {"source":"LICENSE","written":true|false}
# Exit:  0 when LICENSE was created and staged; 1 when LICENSE already exists
#        or is tracked (nothing changes); 2 on a usage, environment or I/O
#        error, where a failed write leaves no LICENSE. Diagnostics go to
#        stderr.
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
  local year="$2" holder="$3" err root tracked
  # Corrupt or unreadable metadata and unsafe ownership fail like "outside a
  # repo", so only git's not-a-repository message means no worktree. The C
  # locale keeps that message in English whatever the caller's locale. Bash
  # captures one stream per substitution: the first call takes git's
  # diagnostic, the second the root, with any warning on stderr.
  if ! err=$(LC_ALL=C LANGUAGE=C git rev-parse --show-toplevel 2>&1 >/dev/null); then
    case "$err" in
      *"fatal: not a git repository (or any "*) echo "error: not inside a git worktree — run from within the consumer repo" >&2 ;;
      *) echo "error: git cannot read this repository: ${err:-git rev-parse failed silently} — fix it ('git status' shows the same error), then re-run" >&2 ;;
    esac
    exit 2
  fi
  if ! root=$(git rev-parse --show-toplevel); then
    echo "error: git rev-parse failed after succeeding once — run 'git status', fix the repository, then re-run" >&2
    exit 2
  fi
  cd "$root"
  if [[ -e LICENSE || -L LICENSE ]]; then refuse "LICENSE already exists"; fi
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
  # Staged here, so the onboarding commit takes this LICENSE.
  if ! git add -- LICENSE; then
    echo "error: wrote LICENSE but could not stage it — fix the cause git printed above, then run 'git add -f LICENSE' (-f stages it even when a .gitignore pattern matches LICENSE) and re-run the commit step" >&2
    exit 2
  fi
  printf '{"source":"LICENSE","written":true}\n'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
