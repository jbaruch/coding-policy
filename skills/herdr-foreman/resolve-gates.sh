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
# be parsed is a repair, never an empty map. A declared path, a note or a
# workflow filename carrying a control character, or a path or filename
# carrying a backtick, is malformed: the GATES block renders them into every
# brief's Markdown, where either breaks the block.

set -euo pipefail

die() { echo "resolve-gates: $*" >&2; exit 2; }

main() {
  [ $# -eq 1 ] || die "usage: resolve-gates.sh <checkout>"
  local checkout="$1"
  [ -d "$checkout" ] || die "'${checkout}' is not a directory -- pass the repository checkout"

  python3 - "$checkout" <<'PY'
import json, os, stat, sys

checkout = sys.argv[1]

path = os.path.join(checkout, ".herdr", "gates.json")


def refuse_unrenderable(value, label, code_span, remedy):
    """Refuse text the GATES block cannot carry intact.

    Every brief renders these values into Markdown. A control character -- a
    newline, a NUL, an escape -- injects lines into every worker's brief or
    breaks path resolution, and so does a lone surrogate JSON can smuggle in;
    a backtick closes the code span a path renders in.
    """
    bad = sorted({char for char in value
                  if ord(char) < 0x20 or 0x7f <= ord(char) <= 0x9f or 0xd800 <= ord(char) <= 0xdfff
                  or (code_span and char == "`")})
    if bad:
        sys.stderr.write("resolve-gates: {} {!r} carries {}, which the briefs' Markdown GATES block "
                         "cannot render intact. {}\n".format(
                             label, value, ", ".join(repr(char) for char in bad), remedy))
        raise SystemExit(2)


declared, instructions, runners, notes, missing = False, [], [], None, []

# A location the platform defines, not a filename anyone guessed. A repo with
# no workflows directory has no workflows; any other failure to list one is a
# tool error, never an empty list. Symlinks are not workflow files, and a
# symlinked directory is not the workflows location.
workflows = []
workflow_dir = os.path.join(checkout, ".github", "workflows")
# A symlinked workflows directory is not the platform's location: its files
# live elsewhere, possibly outside the checkout, so it lists nothing. Only an
# absent path -- no entry, or a `.github` that is a file -- means no workflows;
# any other failure to probe it is a tool error.
try:
    probe = os.lstat(workflow_dir)
except (FileNotFoundError, NotADirectoryError):
    probe = None
except OSError as exc:
    sys.stderr.write("resolve-gates: cannot inspect {}: {} -- check its permissions.\n".format(workflow_dir, exc))
    raise SystemExit(2)
if probe is not None and stat.S_ISDIR(probe.st_mode):
    try:
        with os.scandir(workflow_dir) as entries:
            for entry in entries:
                if entry.name.endswith((".yml", ".yaml")) and entry.is_file(follow_symlinks=False):
                    workflows.append(".github/workflows/" + entry.name)
    except OSError as exc:
        sys.stderr.write("resolve-gates: cannot list {}: {} -- check its permissions.\n".format(workflow_dir, exc))
        raise SystemExit(2)
workflows.sort()
for entry in workflows:
    refuse_unrenderable(entry, "workflow file", code_span=True,
                        remedy="Rename the workflow file without control characters or backticks.")

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
        for item in value:
            refuse_unrenderable(item, "{}'s {} entry".format(path, key), code_span=True,
                                remedy="Remove those characters from the declared path, renaming "
                                       "the file it names if needed.")
        target.extend(value)
    notes = document.get("notes")
    if notes is not None and (not isinstance(notes, str) or not notes.strip()):
        sys.stderr.write("resolve-gates: {}'s notes is one non-empty line, or absent.\n".format(path))
        raise SystemExit(2)
    if notes is not None:
        refuse_unrenderable(notes, "{}'s notes".format(path), code_span=False,
                            remedy="Rewrite notes as one line without control characters.")
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
