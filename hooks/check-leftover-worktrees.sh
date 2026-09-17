#!/usr/bin/env bash
# Report worktrees holding work that nothing in git preserves, at session start.
#
# The safety net under skills/release/check-leftovers.sh. That script gates the
# release flow, which catches a leftover the next time someone ships; this
# catches it the next time someone opens a session, whichever comes first. A
# pane-label change sat uncommitted for nine days with neither in place, on a
# branch that read as merged.
#
# Detection is not reimplemented here. The release script owns the predicate and
# this hook reads its JSON, so the two cannot drift into disagreeing about what
# a leftover is. The hook reports only the "abandoned" verdict -- work in
# progress is what a session is for, and a hook that nags about it gets turned
# off.
#
# Complementary to hooks/stop-handoff-hygiene.sh, which covers the inverse case
# and must not be folded into this one. That hook lists worktrees safe to
# REMOVE, so its `worktree_is_spent` returns early with SPENT_REASON="dirty" and
# a dirty worktree is deliberately left out of the report -- correct for its
# purpose, telling nobody to delete a tree holding work. Its separate dirty-tree
# line runs a bare `git status`, so it sees the current worktree alone. Between
# them a dirty OTHER worktree is the one state neither reports, and it is the
# only state where work exists that git does not hold.
#
# Design choices, shared with hooks/check-git-sync.sh:
#   - It DOES something (runs the detector), it does not re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn tax.
#   - No network and no `gh`: the predicate is three local git signals.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read (the script needs none of it).
#   stdout: one JSON object {"additionalContext": "<status>"} whose text begins
#           with the "Session-start status — " marker
#           (rules/hook-action-reporting.md), emitted ONLY when at least one
#           worktree carries the abandoned verdict. Silent otherwise, which
#           covers the common cases: a clean tree, work in progress, a
#           non-repository, and a checkout with no worktrees but its own.
#   stderr: every line the detector wrote, relayed with a `detector: ` prefix, so
#           a path whose age it could not read stays visible here too.
#   exit  : always 0. A missing detector, an unresolvable repo, a detector
#           tool-error (rc 2), or output missing any documented envelope or entry
#           field emits an actionable stderr warning and no-ops. A broken
#           detector stays visible rather than reading as "nothing abandoned",
#           which is the reassuring answer and the one report this hook exists to
#           rule out (rules/error-handling.md Shell Error Handling).
#   env   : LEFTOVERS_MIN_AGE_HOURS passes through to the detector.
set -euo pipefail

warn() { printf 'check-leftover-worktrees: %s\n' "$1" >&2; }

main() {
  command -v git >/dev/null || return 0
  git rev-parse --git-dir >/dev/null 2>&1 || return 0

  local here detector
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  detector="${here}/../skills/release/check-leftovers.sh"
  if [[ ! -f "$detector" || ! -r "$detector" ]]; then
    warn "detector not readable at ${detector} — reinstall the plugin so session start can report abandoned worktrees"
    return 0
  fi

  local scratch
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/leftover-hook.XXXXXX")" || {
    warn "cannot create a temporary directory under ${TMPDIR:-/tmp} — session start cannot report abandoned worktrees until it is writable"
    return 0
  }
  # shellcheck disable=SC2064  # $scratch is expanded now, on purpose: the trap
  # must name the directory this call created, not whatever the variable holds
  # when it fires.
  trap "rm -rf '$scratch'" RETURN

  # rc 1 is the detector's finding, not a failure: `|| status=$?` keeps `set -e`
  # from aborting on the very outcome this hook exists to report. The detector's
  # stderr is captured rather than discarded, and relayed: it warns on a path
  # whose age it could not read, and that warning is the difference between an
  # under-reported age and a hook that looks like it found nothing.
  local payload status=0 line
  payload="$(bash "$detector" 2>"${scratch}/detector-stderr")" || status=$?
  if [[ -s "${scratch}/detector-stderr" ]]; then
    while IFS= read -r line; do
      [[ -n "$line" ]] && warn "detector: ${line}"
    done < "${scratch}/detector-stderr"
  fi
  if [[ "$status" -eq 2 || -z "$payload" ]]; then
    warn "detector could not read this repository's worktrees — run 'bash ${detector}' directly to see why"
    return 0
  fi

  command -v python3 >/dev/null || { warn "python3 not found on PATH — cannot read the detector's JSON"; return 0; }

  local notice
  # shellcheck disable=SC2016  # The single quotes are the point: the python
  # program must reach the interpreter verbatim, with no shell expansion of the
  # $-free but brace-heavy text inside it.
  notice="$(printf '%s' "$payload" | python3 -c '
import json, sys

MALFORMED = 3
ENVELOPE = ("ok", "self", "others", "blocking")
ENTRY = ("path", "branch", "age_hours", "verdict")


def reject(why):
    sys.stderr.write("{}\n".format(why))
    sys.exit(MALFORMED)


try:
    doc = json.load(sys.stdin)
except ValueError as exc:
    reject("not JSON: {}".format(exc))

# Validated before classification, never after. An entry missing "verdict" would
# otherwise read as not-abandoned, and "nothing abandoned" is the reassuring
# answer a broken detector must never be able to give.
if not isinstance(doc, dict):
    reject("expected a JSON object, got {}".format(type(doc).__name__))
missing = [k for k in ENVELOPE if k not in doc]
if missing:
    reject("envelope is missing {}".format(", ".join(missing)))
if not isinstance(doc["others"], list):
    reject("the others field is not a list")
if doc["self"] is not None and not isinstance(doc["self"], dict):
    reject("the self field is neither an object nor null")

for entry in list(doc["others"]) + ([doc["self"]] if doc["self"] is not None else []):
    if not isinstance(entry, dict):
        reject("a worktree entry is not an object")
    absent = [k for k in ENTRY if k not in entry]
    if absent:
        reject("a worktree entry is missing {}".format(", ".join(absent)))

rows = []
me = doc["self"]
if me is not None and me["verdict"] == "abandoned":
    rows.append((me["path"], me["branch"], me["age_hours"], True))
for row in doc["others"]:
    if row["verdict"] == "abandoned":
        rows.append((row["path"], row["branch"], row["age_hours"], False))
if not rows:
    sys.exit(0)

lines = ["Session-start status — {} worktree(s) hold uncommitted work on a branch "
         "carrying no commits of its own; nothing in git preserves it:".format(len(rows))]
for path, branch, age, is_self in sorted(rows, key=lambda r: -r[2]):
    lines.append("  - {}{} on `{}`, last written {}h ago".format(
        path, " (this session)" if is_self else "", branch, age))
lines.append("Inspect with `git -C <path> status`, then commit, stash or gitignore it. "
             "`skills/release/check-leftovers.sh` refuses to start a release until this clears.")
print("\n".join(lines))
')" || { warn "the detector's output is not the JSON envelope this hook reads (see above) — run 'bash ${detector}' directly to see what it printed"; return 0; }

  [[ -n "$notice" ]] || return 0

  if command -v jq >/dev/null; then
    jq -n --arg c "$notice" '{additionalContext: $c}' || warn "jq failed to encode the notice"
  else
    printf '%s' "$notice" | python3 -c 'import json,sys; print(json.dumps({"additionalContext": sys.stdin.read()}))' \
      || warn "could not encode the notice as JSON"
  fi
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
