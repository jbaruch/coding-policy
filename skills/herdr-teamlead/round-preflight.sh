#!/usr/bin/env bash
# Every deterministic check a round start owes, in one call.
#
# SKILL.md Steps 1-9 precede the first dispatch, and each one was a separate
# lead turn that shipped the lead's whole accumulated context to run a script
# and read its exit code. The checks are deterministic -- Herdr reachable, the
# roster measured, authority verified, a cadence due, worktrees pruned -- so
# rules/script-delegation.md puts them here, and its Precheck Gating shape says
# what to emit: one payload saying whether the agent is needed and what it
# needs (#445 §1).
#
# This composes the existing owner scripts. It reimplements none of them, and
# each one's contract stays its own.
#
# Usage: round-preflight.sh --repo <owner/repo> --checkout <path> [--now ISO]
#                          [--state FILE] [--config FILE] [--no-measure]
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object --
#     {"schema_version": 1, "ready": bool, "blocking": ["<reason>", ...],
#      "due": ["<cadence>", ...], "checks": {"<name>": {...}}}
#   stderr: each check's own diagnostic, relayed verbatim.
#
# `ready` is false when any check blocks a dispatch. `due` names a cadence the
# lead owes before planning; it does not block. A blocking check is reported
# with the command that produced it, so the lead re-runs that one rather than
# the whole preflight.
#
# Exit 0 when every check ran and none blocks. Exit 1 when a check blocks the
# round -- a verdict, not a failure. Exit 2 on a usage or tool error, where the
# preflight could not answer.
#
# `--no-measure` skips the headroom snapshot, which is the one check that
# writes. Everything else here is read-only.

# `-e` is dropped under rules/error-handling.md's aggregate-reporting carve-out:
# each check below is independent, every exit code is captured explicitly, and
# the aggregate decides the exit status. `-u` and `-o pipefail` stay.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd && printf x)"
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

SCRATCH=""
# An EXIT trap's final status becomes the script's, so cleanup ends on zero and
# never rewrites the verdict (rules/error-handling.md Shell Error Handling).
cleanup() { [ -n "$SCRATCH" ] && rm -rf "$SCRATCH"; return 0; }
trap cleanup EXIT

die() { echo "round-preflight: $*" >&2; exit 2; }

emit() { # <json-fragments-file>
  python3 - "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    checks = json.load(handle)
blocking = [row["reason"] for row in checks.values() if row.get("reason")]
due = [name for name, row in checks.items() if row.get("due")]
print(json.dumps({"schema_version": 1, "ready": not blocking,
                  "blocking": blocking, "due": sorted(due), "checks": checks},
                 sort_keys=True))
PY
}

main() {
  local repo="" checkout="" now="" state="" config="" measure=1
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) repo="${2-}"; shift 2 || die "--repo needs <owner/repo>" ;;
      --checkout) checkout="${2-}"; shift 2 || die "--checkout needs a path" ;;
      --now) now="${2-}"; shift 2 || die "--now needs an ISO timestamp" ;;
      --state) state="${2-}"; shift 2 || die "--state needs a file" ;;
      --config) config="${2-}"; shift 2 || die "--config needs a file" ;;
      --no-measure) measure=0; shift ;;
      -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
      *) die "unknown argument '$1' -- see --help" ;;
    esac
  done
  [ -n "$repo" ] || die "pass --repo <owner/repo>: authority is verified per repository"
  [ -n "$checkout" ] || die "pass --checkout <path>: the shared checkout worktrees are pruned against"

  local scratch
  # A temp dir spliced into later file operations fails loudly on first use, but
  # a failed mkdir here would leave every path below writing somewhere else.
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/round-preflight.XXXXXX")" \
    || die "cannot create a temporary directory under ${TMPDIR:-/tmp}"
  SCRATCH="$scratch"

  local common=() clock=()
  [ -n "$state" ] && common+=(--state "$state")
  [ -n "$config" ] && common+=(--config "$config")
  [ -n "$now" ] && clock+=(--now "$now")

  local results="${scratch}/checks.json"
  printf '{}' > "$results" || die "cannot write to ${scratch}"

  record() { # <name> <status> <reason-or-empty> <due:0|1> <detail-file-or-empty>
    python3 - "$results" "$1" "$2" "$3" "$4" "${5-}" <<'PY'
import json, sys
path, name, status, reason, due, detail = sys.argv[1:7]
with open(path, encoding="utf-8") as handle:
    checks = json.load(handle)
row = {"status": status, "due": due == "1"}
if reason:
    row["reason"] = reason
if detail:
    try:
        with open(detail, encoding="utf-8") as handle:
            row["detail"] = json.load(handle)
    except (OSError, ValueError):
        row["detail"] = None
checks[name] = row
with open(path, "w", encoding="utf-8") as handle:
    json.dump(checks, handle)
PY
  }

  # 1. Mode. A standalone agent runs none of this; the caller decides, and the
  #    preflight only reports what it observed.
  if [ -n "${HERDR_ENV:-}" ]; then
    record mode ok "" 0 ""
  else
    record mode standalone "HERDR_ENV is unset: this is not a Herdr team round, and a standalone agent does the task directly" 0 ""
    emit "$results" || die "cannot assemble the preflight payload"
    return 1
  fi

  # 2. Roster.
  local rc
  bash "${HERE}/roster.sh" > "${scratch}/roster.json" 2>"${scratch}/roster.err"
  rc=$?
  cat "${scratch}/roster.err" >&2
  if [ "$rc" -eq 0 ]; then
    record roster ok "" 0 "${scratch}/roster.json"
  else
    record roster failed "roster.sh exited ${rc}; re-run it and read its diagnostic before planning" 0 ""
  fi

  # 3. Authority for this repo.
  bash "${HERE}/verify-authority.sh" "$repo" > "${scratch}/authority.json" 2>"${scratch}/authority.err"
  rc=$?
  cat "${scratch}/authority.err" >&2
  if [ "$rc" -eq 0 ]; then
    record authority ok "" 0 "${scratch}/authority.json"
  else
    record authority failed "verify-authority.sh exited ${rc} for ${repo}; an unanswerable authority check is not permission" 0 ""
  fi

  # 4. Headroom. The one check that writes, and the one the planner reads.
  if [ "$measure" -eq 1 ]; then
    bash "${HERE}/teamlead.sh" "${common[@]+"${common[@]}"}" measure "${clock[@]+"${clock[@]}"}" \
      > "${scratch}/measure.json" 2>"${scratch}/measure.err"
    rc=$?
    cat "${scratch}/measure.err" >&2
    if [ "$rc" -eq 0 ]; then
      record headroom ok "" 0 "${scratch}/measure.json"
    else
      record headroom failed "teamlead measure exited ${rc}; a seat cannot be ranked on an unmeasured roster" 0 ""
    fi
  else
    record headroom skipped "" 0 ""
  fi

  # 5. Capability-table cadence. Due is not blocking: the lead refreshes it
  #    before planning, and a fleet that dispatched nothing never comes due.
  bash "${HERE}/teamlead.sh" "${common[@]+"${common[@]}"}" capability-check "${clock[@]+"${clock[@]}"}" \
    > "${scratch}/capability.json" 2>"${scratch}/capability.err"
  rc=$?
  cat "${scratch}/capability.err" >&2
  if [ "$rc" -eq 0 ]; then
    local capability_due
    capability_due="$(python3 -c 'import json,sys; print("1" if json.load(open(sys.argv[1]))["due"] else "0")' "${scratch}/capability.json")"
    record capability ok "" "$capability_due" "${scratch}/capability.json"
  else
    record capability failed "teamlead capability-check exited ${rc}; the table's cadence is unknown" 0 ""
  fi

  # 6. Where this repo records its gates. Resolved once here so five workers do
  #    not each spend turns finding the same files.
  bash "${HERE}/resolve-gates.sh" "$checkout" > "${scratch}/gates.json" 2>"${scratch}/gates.err"
  rc=$?
  cat "${scratch}/gates.err" >&2
  if [ "$rc" -eq 0 ]; then
    record gates ok "" 0 "${scratch}/gates.json"
  else
    record gates failed "resolve-gates.sh exited ${rc}; the briefs carry no gate pointers and every worker searches" 0 ""
  fi

  # 7. Worktree hygiene. Pruned every round, before provisioning.
  bash "${HERE}/prune-worktrees.sh" "$checkout" > "${scratch}/prune.json" 2>"${scratch}/prune.err"
  rc=$?
  cat "${scratch}/prune.err" >&2
  case "$rc" in
    0) record worktrees ok "" 0 "${scratch}/prune.json" ;;
    1) record worktrees undecided "prune-worktrees.sh decided nothing; fix its diagnostic and re-run before provisioning" 0 "" ;;
    *) record worktrees failed "prune-worktrees.sh exited ${rc}; git refused a check or a removal" 0 "" ;;
  esac

  local payload
  payload="$(emit "$results")" || die "cannot assemble the preflight payload"
  printf '%s\n' "$payload"
  printf '%s' "$payload" \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["ready"] else 1)'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
