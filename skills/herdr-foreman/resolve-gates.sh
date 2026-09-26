#!/usr/bin/env bash
# Where a repo records its gates, so a worker reads instead of searching.
#
# COMMON.md told every worker "read the repo's contributor instructions and
# configured checks to identify its gates". Five workers in a round each spent
# turns finding the same answer, every round, for something identical across
# them and rarely changed.
#
# Discovery splits in two: FINDING a file and READING it. Reading is the work.
# Finding is waste, and a pointer removes it without putting any file's contents
# into a worker's context.
#
# THE REPO DECLARES ITS GATES. It already declares its trigger surfaces the same
# way, in `.herdr/triggers.json` (rules/agent-team-operation.md: "The repo states
# each trigger surface and its package size in its own trigger declaration"), so
# this reads `.herdr/gates.json` and reports what it holds.
#
# An earlier draft matched a hardcoded list of filenames -- AGENTS.md, Makefile,
# pyproject.toml and so on. That is the enumerated-name failure #480 retired one
# layer down: the set of filenames meaning "runner" is not enumerable, and
# justfile, Taskfile.yml, Earthfile and `bin/check` are all invisible to it. A
# declaration has no such set. The repo owner states the truth once.
#
# Undeclared is a first-class answer, not an error. `.github/workflows` is a
# location GitHub defines rather than a name anyone guessed, so it is still
# reported; everything else comes back `declared: false`, and the brief tells
# the worker to find the gates and name them in its report. The first worker to
# do so gives the owner the declaration to write.
#
# Usage: resolve-gates.sh <checkout>
#
# Declaration shape (`.herdr/gates.json`, all keys optional):
#   {"schema_version": 1,
#    "instructions": ["AGENTS.md"],
#    "runners": ["scripts/run-tests.sh"],
#    "notes": "one line a worker reads before running anything"}
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object --
#     {"schema_version": 1, "declared": bool, "instructions": [...],
#      "runners": [...], "workflows": [...], "notes": <str|null>,
#      "missing": [...], "brief": "<the GATES value, ready to use>"}
#   `brief` is the Markdown list a brief's GATES field carries, built from the
#   present paths only, or the word `undeclared` when the repo declares none.
#   `missing` lists declared paths that are not readable, so a declaration that
#   has rotted says so rather than pointing a worker at nothing.
#   stderr: diagnostics.
#
# Exit 0 whether or not the repo declared anything. Exit 2 on a usage error, an
# unreadable checkout, or a malformed declaration -- a declaration that cannot
# be parsed is a repair, never an empty map.

set -euo pipefail

die() { echo "resolve-gates: $*" >&2; exit 2; }

main() {
  [ $# -eq 1 ] || die "usage: resolve-gates.sh <checkout>"
  local checkout="$1"
  [ -d "$checkout" ] || die "'${checkout}' is not a directory -- pass the repository checkout"

  local workflows=() found listing
  # A location the platform defines, not a filename anyone guessed. A repo
  # with no workflows directory has no workflows; any other failure to list
  # one is a tool error, never an empty list.
  if [ -d "${checkout}/.github/workflows" ]; then
    listing="$(find "${checkout}/.github/workflows" -maxdepth 1 -type f \
                 \( -name '*.yml' -o -name '*.yaml' \) -print)" \
      || die "cannot list ${checkout}/.github/workflows -- check its permissions"
    while IFS= read -r found; do
      if [ -n "$found" ]; then workflows+=("${found#"${checkout}/"}"); fi
    done <<< "$(printf '%s\n' "$listing" | sort)"
  fi

  python3 - "$checkout" "${#workflows[@]}" ${workflows[@]+"${workflows[@]}"} <<'PY'
import json, os, sys

checkout = sys.argv[1]
count = int(sys.argv[2])
workflows = sorted(sys.argv[3:3 + count])

path = os.path.join(checkout, ".herdr", "gates.json")
declared, instructions, runners, notes, missing = False, [], [], None, []

try:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
except FileNotFoundError:
    document = None
except (OSError, UnicodeDecodeError) as exc:
    sys.stderr.write("resolve-gates: cannot read {}: {}. Restore a readable UTF-8 "
                     "declaration, or remove it to report the repo as undeclared.\n".format(path, exc))
    raise SystemExit(2)
except json.JSONDecodeError as exc:
    sys.stderr.write("resolve-gates: {} is not valid JSON ({}). Repair the declaration; a "
                     "declaration that cannot be parsed is never an empty map.\n".format(path, exc.msg))
    raise SystemExit(2)

if document is not None:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        sys.stderr.write("resolve-gates: {} needs schema_version 1 and a JSON object.\n".format(path))
        raise SystemExit(2)
    if set(document) - {"schema_version", "instructions", "runners", "notes"}:
        sys.stderr.write("resolve-gates: {} accepts instructions, runners and notes.\n".format(path))
        raise SystemExit(2)
    for key, target in (("instructions", instructions), ("runners", runners)):
        value = document.get(key, [])
        if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value):
            sys.stderr.write("resolve-gates: {}'s {} lists repo-relative paths.\n".format(path, key))
            raise SystemExit(2)
        target.extend(value)
    notes = document.get("notes")
    if notes is not None and (not isinstance(notes, str) or not notes.strip()):
        sys.stderr.write("resolve-gates: {}'s notes is one non-empty line, or absent.\n".format(path))
        raise SystemExit(2)
    declared = True
    # A declared path is a pointer a worker follows, so it must stay inside the
    # checkout: an absolute path, a `..` or a symlink out of the tree is refused.
    root = os.path.realpath(checkout)
    for entry in instructions + runners:
        resolved = os.path.realpath(os.path.join(checkout, entry))
        if os.path.isabs(entry) or os.path.commonpath([root, resolved]) != root:
            sys.stderr.write("resolve-gates: {} names {!r}, which resolves outside the checkout. "
                             "Declare repo-relative paths inside it.\n".format(path, entry))
            raise SystemExit(2)
    # A declaration that has rotted says so, rather than pointing at nothing.
    for entry in instructions + runners:
        candidate = os.path.join(checkout, entry)
        if not (os.path.isfile(candidate) and os.access(candidate, os.R_OK)):
            missing.append(entry)

def brief():
    if not declared:
        return "undeclared"
    lines = []
    for label, entries in (("Instructions", instructions), ("Runners", runners), ("Workflows", workflows)):
        present = [entry for entry in sorted(entries) if entry not in missing]
        if present:
            lines.append("- {}: {}".format(label, ", ".join("`{}`".format(entry) for entry in present)))
    if notes:
        lines.append("- Notes: {}".format(notes))
    return "\n".join(lines) if lines else "undeclared"


print(json.dumps({"schema_version": 1, "declared": declared,
                  "instructions": sorted(instructions), "runners": sorted(runners),
                  "workflows": workflows, "notes": notes, "missing": sorted(missing),
                  "brief": brief()},
                 sort_keys=True))
PY
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
