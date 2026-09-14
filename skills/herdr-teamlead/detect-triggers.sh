#!/usr/bin/env bash
# Report which specialist consultation triggers a change fires.
#
# `rules/agent-team-operation.md` Team Composition names five triggers. The
# exhaustion one is enforced where it happens, in `teamlead diagnose` and the
# judge dispatch paths. The other four described a condition the lead was
# expected to notice in its own diff, which is the state #408 was filed to
# replace: a check nobody runs does not exist
# (`rules/language-diagnostics.md` Gate It Deterministically). Reading a diff
# against declared path sets is one right answer per input, so it lives here
# (`rules/script-delegation.md`).
#
# What fires a trigger, per `references/specialists.md`:
#   architect      a package directory the base did not have, or a declared
#                  package whose changed lines exceed the repo's threshold
#   security       a changed path in the declared trust-boundary set
#   ux-product     a changed path in the declared user-facing command surface
#   documentation  an added path in the declared user-facing document set
#
# Every set is the consuming repo's to declare, in a trigger config the repo
# commits. Without one this script refuses rather than reporting "nothing
# fired": a repo that declared nothing has not proved its triggers are quiet.
#
# Config (JSON), default `.herdr-triggers.json` at the checkout root:
#   {"schema_version": 1,
#    "architect":     {"package_globs": ["src/*"], "changed_lines": 400},
#    "security":      {"paths": ["**/auth/**"]},
#    "ux_product":    {"paths": ["src/cli/**"]},
#    "documentation": {"paths": ["docs/**"]}}
# A section may be omitted; its trigger then reports `undeclared` and never
# fires. `changed_lines` counts added plus removed lines across a package and
# must be a positive integer. Patterns match with Python `fnmatch` against
# repository-relative POSIX paths, so `*` and `**` both cross separators.
#
# Contract:
#   argv  : <checkout> <base-ref> <head-ref> [config]
#   stdout: one JSON object —
#           {"base":"<sha>","head":"<sha>","config":"<abs>","changed":N,
#            "fired":[{"trigger":"<name>","profile":"<profile>",
#                      "deliverable":"<what it must produce>",
#                      "evidence":["<path or package>", ...]}],
#            "quiet":["<name>", ...],
#            "undeclared":["<name>", ...]}
#           `fired` is what the lead consults or records a staffing decision
#           for; `undeclared` is what the repo has not described.
#   stderr: diagnostics only.
#   exit  : 0 every declared trigger was decided (fired or quiet),
#           1 precondition unmet (usage, git or python3 absent, not a repo,
#             unreadable or malformed config, unresolvable ref),
#           2 git failed while reading the diff; nothing was decided.
set -euo pipefail

ERRFILE=""
NUMSTAT=""
STATUS_LIST=""
BASE_PATHS=""

warn() { printf 'detect-triggers: %s\n' "$1" >&2; }

cleanup() {
  local f
  for f in "$ERRFILE" "$NUMSTAT" "$STATUS_LIST" "$BASE_PATHS"; do
    if [[ -n "$f" ]] && ! rm -f "$f"; then
      warn "could not remove temp file ${f} — remove it by hand"
    fi
  done
  return 0
}

# Echo the resolved commit for <ref>, or return 1 with a diagnostic.
resolve_ref() { # <checkout> <ref> <label>
  local out rc=0
  out="$(git -C "$1" rev-parse --verify --quiet "${2}^{commit}" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ -z "$out" ]]; then
    warn "cannot resolve ${3} ref '${2}': $(tr '\n' ' ' < "$ERRFILE")"
    return 1
  fi
  printf '%s' "$out"
}

main() {
  if (( $# < 3 || $# > 4 )); then
    warn "usage: detect-triggers.sh <checkout> <base-ref> <head-ref> [config]"
    return 1
  fi
  local checkout="$1" base_ref="$2" head_ref="$3" config="${4:-}"
  local tool
  for tool in git python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      warn "${tool} not found on PATH"
      return 1
    fi
  done
  ERRFILE="$(mktemp)"
  trap cleanup EXIT
  if [[ ! -d "$checkout" ]] || ! git -C "$checkout" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "'${checkout}' is not a git work tree — pass the checkout holding both refs"
    return 1
  fi
  local root
  root="$(git -C "$checkout" rev-parse --show-toplevel)"
  if [[ -z "$config" ]]; then
    config="${root}/.herdr-triggers.json"
  fi
  if [[ ! -r "$config" ]]; then
    warn "no trigger config at ${config} — declare this repo's package, trust-boundary, command-surface and document paths there, or pass one; a repo that declares nothing has not proved its triggers are quiet"
    return 1
  fi

  local base head
  base="$(resolve_ref "$checkout" "$base_ref" base)" || return 1
  head="$(resolve_ref "$checkout" "$head_ref" head)" || return 1

  # `-z` keeps a path holding a newline in one field. The status is read
  # separately because an added path fires the document trigger while a
  # modified one does not.
  NUMSTAT="$(mktemp)"; STATUS_LIST="$(mktemp)"; BASE_PATHS="$(mktemp)"
  local rc=0
  git -C "$checkout" --no-pager diff --no-color --no-renames -z --numstat "$base" "$head" -- >"$NUMSTAT" 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    warn "\`git diff --numstat\` failed: $(tr '\n' ' ' < "$ERRFILE") — nothing was decided"
    return 2
  fi
  rc=0
  git -C "$checkout" --no-pager diff --no-color --no-renames -z --name-status "$base" "$head" -- >"$STATUS_LIST" 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    warn "\`git diff --name-status\` failed: $(tr '\n' ' ' < "$ERRFILE") — nothing was decided"
    return 2
  fi

  # The base tree's own paths: a package is new when the base lacked it, not
  # when every changed file inside it happens to be an addition.
  rc=0
  git -C "$checkout" ls-tree -r -z --name-only "$base" >"$BASE_PATHS" 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    warn "\`git ls-tree\` failed: $(tr '\n' ' ' < "$ERRFILE") — nothing was decided"
    return 2
  fi

  rc=0
  python3 - "$config" "$base" "$head" "$NUMSTAT" "$STATUS_LIST" "$BASE_PATHS" <<'PY' || rc=$?
import fnmatch
import json
import posixpath
import sys

CONFIG, BASE, HEAD, NUMSTAT, STATUS, BASE_PATHS = sys.argv[1:7]

PROFILES = {
    "architect": ("Architect", "The intended boundaries, the options and consequences, and the verification each boundary needs"),
    "security": ("Security", "A bounded threat assessment against that boundary"),
    "ux_product": ("UX and product", "The flow, the alternatives considered, and acceptance criteria"),
    "documentation": ("Documentation", "A draft verified against the shipped behavior, never against intent"),
}


def fail(message):
    sys.stderr.write("detect-triggers: {}\n".format(message))
    raise SystemExit(1)


def read_config():
    try:
        with open(CONFIG, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail("cannot read trigger config {}: {}. Save valid UTF-8 JSON.".format(CONFIG, exc))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        fail("trigger config needs schema_version 1 and a JSON object; update the owner before relying on it.")
    unknown = set(data) - {"schema_version"} - set(PROFILES)
    if unknown:
        fail("trigger config names unknown sections {}; the triggers are {}.".format(
            ", ".join(sorted(unknown)), ", ".join(sorted(PROFILES))))
    for name in PROFILES:
        section = data.get(name)
        if section is None:
            continue
        if not isinstance(section, dict):
            fail("trigger config section {} must be an object.".format(name))
        if name == "architect":
            extra = set(section) - {"package_globs", "changed_lines"}
            if extra or "package_globs" not in section or "changed_lines" not in section:
                fail("the architect section needs package_globs and changed_lines, and nothing else.")
            patterns(section["package_globs"], name)
            size = section["changed_lines"]
            if type(size) is not int or size < 1:
                fail("architect changed_lines must be a positive integer: the size a package must exceed.")
        else:
            if set(section) != {"paths"}:
                fail("the {} section needs paths, and nothing else.".format(name))
            patterns(section["paths"], name)
    return data


def patterns(value, name):
    if not isinstance(value, list) or not value:
        fail("the {} section needs a non-empty list of patterns.".format(name))
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            fail("every {} pattern is a non-empty string.".format(name))
    return value


def nul_fields(path):
    with open(path, "rb") as handle:
        parts = handle.read().decode("utf-8", "surrogateescape").split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def numstat_records(path):
    """`--numstat -z` emits one NUL-terminated record per file, its added,
    removed and path tab-separated inside it."""
    rows = []
    for record in nul_fields(path):
        added, _, rest = record.partition("\t")
        removed, _, name = rest.partition("\t")
        if not name:
            fail("git emitted a numstat record this reader cannot split; report it as a bug.")
        rows.append((added, removed, name))
    return rows


def status_records(path):
    """`--name-status -z` emits the status and the path as separate fields."""
    parts = nul_fields(path)
    if len(parts) % 2:
        fail("git emitted a name-status record this reader cannot split; report it as a bug.")
    return [(parts[i], parts[i + 1]) for i in range(0, len(parts), 2)]


def matches(path, globs):
    return any(fnmatch.fnmatch(path, pattern) for pattern in globs)


def main():
    config = read_config()
    counts = numstat_records(NUMSTAT)
    statuses = dict((path, status) for status, path in status_records(STATUS))
    changed = [path for _added, _removed, path in counts]

    fired, quiet, undeclared = [], [], []

    def decide(name, evidence):
        if config.get(name) is None:
            undeclared.append(name)
        elif evidence:
            profile, deliverable = PROFILES[name]
            fired.append({"trigger": name, "profile": profile,
                          "deliverable": deliverable, "evidence": sorted(set(evidence))})
        else:
            quiet.append(name)

    architect = config.get("architect")
    packages = []
    if architect is not None:
        base_dirs = set()
        for path in nul_fields(BASE_PATHS):
            parts = path.split("/")
            for depth in range(1, len(parts)):
                base_dirs.add("/".join(parts[:depth]))
        totals = {}
        for added, removed, path in counts:
            for pattern in architect["package_globs"]:
                package = None
                parts = path.split("/")
                for depth in range(1, len(parts)):
                    candidate = "/".join(parts[:depth])
                    if fnmatch.fnmatch(candidate, pattern):
                        package = candidate
                        break
                if package is None:
                    continue
                # A binary file's counts are "-"; it changed, its size is unknown.
                lines = 0
                for value in (added, removed):
                    if value.isdigit():
                        lines += int(value)
                entry = totals.setdefault(package, {"lines": 0, "new": package not in base_dirs})
                entry["lines"] += lines
                break
        for package, entry in totals.items():
            if entry["new"] or entry["lines"] > architect["changed_lines"]:
                packages.append(package)
    decide("architect", packages)

    for name in ("security", "ux_product"):
        section = config.get(name)
        hits = [] if section is None else [p for p in changed if matches(p, section["paths"])]
        decide(name, hits)

    section = config.get("documentation")
    added_docs = ([] if section is None
                  else [p for p in changed if statuses.get(p) == "A" and matches(p, section["paths"])])
    decide("documentation", added_docs)

    print(json.dumps({"base": BASE, "head": HEAD, "config": posixpath.abspath(CONFIG),
                      "changed": len(changed), "fired": fired,
                      "quiet": sorted(quiet), "undeclared": sorted(undeclared)},
                     sort_keys=True))


main()
PY
  return "$rc"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
