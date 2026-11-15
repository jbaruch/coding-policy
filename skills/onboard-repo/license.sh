#!/usr/bin/env bash
# Report a consumer repo's license state, and with --write add an MIT LICENSE.
#
# A repo already licensed by a LICENSE/COPYING file, or declaring a license in
# a manifest (package.json, pyproject.toml, Cargo.toml, .tessl-plugin/plugin.json,
# agent-plugin.yaml), keeps that choice. A README "License" heading is reported
# as "readme" for the caller to read, since only its text says whether it names
# a license. The report also lists commit authors other than the operator (git
# config user.name/user.email) and known bot and agent identities, so the
# caller can judge whether the history is the operator's own work before
# adding MIT.
#
# Usage: license.sh [--write]
#   --write   Write LICENSE as MIT for the current git user.name and the year
#             of the first commit, and stage it. Refuses (exit 1) when a
#             license file or manifest declaration already exists.
# Out:   one JSON object on stdout:
#          {"license":"file|declared|readme|none","source":"<path>"|null,
#           "outside_authors":["Name <email>",...],"written":true|false}
# Exit:  0 on success; 1 when --write is refused; 2 on a usage or environment
#        error. Diagnostics go to stderr.
set -euo pipefail

usage() { echo "usage: license.sh [--write]" >&2; exit 2; }

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

main() {
  local write=0
  case "$#" in
    0) ;;
    1) [[ "$1" == "--write" ]] || usage; write=1 ;;
    *) usage ;;
  esac
  command -v python3 >/dev/null || { echo "error: python3 not found on PATH — install Python 3 and re-run" >&2; exit 2; }
  local root
  if ! root=$(git rev-parse --show-toplevel); then
    echo "error: not inside a git worktree — run from within the consumer repo" >&2
    exit 2
  fi
  cd "$root"

  # The detection reads files and git history only; python3 owns the parsing
  # and the JSON (rules/script-delegation.md Script Requirements).
  local report
  if ! report=$(python3 -I - <<'PY'
import json, re, subprocess, sys
from pathlib import Path

license, source = "none", None
for entry in sorted(Path(".").iterdir()):
    if entry.is_file() and re.fullmatch(r"(?i)(licen[cs]e|copying)(\.(md|txt|rst))?", entry.name):
        license, source = "file", entry.name
        break

def declared():
    pkg = Path("package.json")
    if pkg.is_file():
        try:
            if json.loads(pkg.read_text()).get("license"):
                return "package.json"
        except ValueError:
            sys.stderr.write("warning: package.json is not valid JSON; reading no license from it\n")
    for name, pattern in (("pyproject.toml", r"(?m)^\s*license\s*="),
                          ("Cargo.toml", r"(?m)^\s*license(-file)?\s*="),
                          ("agent-plugin.yaml", r"(?m)^license:\s*\S")):
        path = Path(name)
        if path.is_file() and re.search(pattern, path.read_text()):
            return name
    plugin = Path(".tessl-plugin/plugin.json")
    if plugin.is_file():
        try:
            if json.loads(plugin.read_text()).get("license"):
                return ".tessl-plugin/plugin.json"
        except ValueError:
            sys.stderr.write("warning: .tessl-plugin/plugin.json is not valid JSON; reading no license from it\n")
    return None

def readme_heading():
    for readme in sorted(Path(".").glob("README*")):
        if readme.is_file() and re.search(r"(?mi)^#{1,6}\s*licen[cs]e\b", readme.read_text(errors="replace")):
            return readme.name
    return None

if license == "none":
    found = declared()
    if found:
        license, source = "declared", found
    else:
        found = readme_heading()
        if found:
            license, source = "readme", found

operator_name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
operator_email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True).stdout.strip().lower()
bot = re.compile(r"(?i)\[bot\]|dependabot|renovate|github-actions|noreply@anthropic\.com|noreply@openai\.com")
outside = set()
log = subprocess.run(["git", "log", "--format=%aN <%aE>"], capture_output=True, text=True)
if log.returncode == 0:
    for line in log.stdout.splitlines():
        name, _, email = line.rpartition(" <")
        email = email.rstrip(">").lower()
        if bot.search(line) or email == operator_email or (operator_name and name == operator_name):
            continue
        outside.add(line)
elif "does not have any commits" not in log.stderr:
    sys.stderr.write(log.stderr)
    sys.exit(2)

print(json.dumps({"license": license, "source": source, "outside_authors": sorted(outside)}))
PY
  ); then
    echo "error: could not inspect the repository's license state — check that the files above are readable and re-run" >&2
    exit 2
  fi

  local state
  state=$(printf '%s' "$report" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["license"])')
  if (( write == 0 )); then
    printf '%s' "$report" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); d["written"]=False; print(json.dumps(d))'
    return 0
  fi
  if [[ "$state" == "file" || "$state" == "declared" ]]; then
    echo "error: refusing --write — the repo already has a license (${state}); keep it or change it by hand" >&2
    printf '%s' "$report" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); d["written"]=False; print(json.dumps(d))'
    exit 1
  fi

  local holder year roots root_year root_commit
  holder=$(git config user.name) || { echo "error: git user.name is unset — set it with 'git config user.name \"Your Name\"' and re-run" >&2; exit 2; }
  [[ -n "$holder" ]] || { echo "error: git user.name is empty — set it with 'git config user.name \"Your Name\"' and re-run" >&2; exit 2; }
  # An unborn HEAD (no commits yet) dates the license this year; any other
  # failure to read the history stops, since it would misdate the license.
  year=""
  if git rev-parse --verify --quiet HEAD >/dev/null; then
    if ! roots=$(git rev-list --max-parents=0 HEAD); then
      echo "error: could not list the repository's first commits — run 'git fsck', repair the history and re-run" >&2
      exit 2
    fi
    while IFS= read -r root_commit; do
      [[ -n "$root_commit" ]] || continue
      if ! root_year=$(git log -1 --format=%ad --date=format:%Y "$root_commit"); then
        echo "error: could not read the date of first commit ${root_commit} — repair the history and re-run" >&2
        exit 2
      fi
      if [[ -z "$year" || "$root_year" < "$year" ]]; then year="$root_year"; fi
    done <<< "$roots"
  elif ! year=$(date +%Y); then
    echo "error: cannot read the current year" >&2
    exit 2
  fi
  if ! mit_text "$year" "$holder" > LICENSE; then
    echo "error: could not write LICENSE — check the repo root is writable and re-run" >&2
    exit 2
  fi
  # Staged here, so the onboarding commit takes this LICENSE and never an
  # untracked draft that was already in the tree.
  if ! git add -- LICENSE; then
    echo "error: wrote LICENSE but could not stage it — run 'git add LICENSE' and re-run the commit step" >&2
    exit 2
  fi
  printf '%s' "$report" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); d.update(license="file", source="LICENSE", written=True); print(json.dumps(d))'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
