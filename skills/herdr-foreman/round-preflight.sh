#!/usr/bin/env bash
# Every deterministic check a round start owes, in one call.
#
# SKILL.md Steps 1-9 precede the first dispatch, and each one was a separate
# foreman turn that shipped the foreman's whole accumulated context to run a script
# and read its exit code. The checks are deterministic -- Herdr reachable, the
# roster measured, authority verified, a cadence due, worktrees swept -- so
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
# foreman owes before planning; it does not block. A blocking check is reported
# with the command that produced it, so the foreman re-runs that one rather than
# the whole preflight.
#
# Exit 0 when every check ran and none blocks. Exit 1 when a check blocks the
# round -- a verdict, not a failure. Exit 2 on a usage or tool error, where the
# preflight could not answer.
#
# `--no-measure` skips the headroom snapshot, which is the one check that
# writes. Every other check is read-only except the worktree sweep, which
# removes worktrees and deletes local branches under its own contract.
#
# `checks.headroom` and `checks.foreman_tier` are the two rows of ONE composite
# check, foreman-tier-check.py, which owns the dependency between them; its
# docstring states each row's statuses. An absent `foreman` block is
# `unconfigured`, never blocking, whatever headroom reported. A composite run
# that exits non-zero or returns no readable pair fails both rows.
# `--no-measure` reuses the latest snapshot.
#
# `checks.worktrees` (the sweep, sweep-worktrees.sh):
#   ok         no detail when the worktree root does not exist; otherwise the
#              sweep JSON as detail
#   undecided  the sweep decided nothing (exit 1): reason, no detail; or this
#              checkout's own prune decided nothing: reason, sweep JSON detail
#   failed     this checkout's prune failed or returned no readable result,
#              or an error names this checkout
#              or no repository: reason, sweep JSON detail; the sweep's JSON
#              was unreadable or it exited other than 0/1/2: reason, no detail
#   degraded   only another repository failed: sweep JSON detail; not blocking
# The sweep runs under bounded-run.sh for SWEEP_BUDGET_SEC: it fetches every
# repository it finds, and a dead remote would otherwise hold the round. A run
# past the budget is failed (reason names the budget, no detail).
# A detail file that cannot be read turns any status into blocked (reason
# names the file).

# `-e` is dropped under rules/error-handling.md's aggregate-reporting carve-out:
# each check below is independent, every exit code is captured explicitly, and
# the aggregate decides the exit status. `-u` and `-o pipefail` stay.
set -uo pipefail

# Command substitution strips every trailing newline, so the script directory
# never passes through one bare: parameter expansion derives it (#487), and a
# sentinel carries `pwd` across the strip (#466).
case "${BASH_SOURCE[0]}" in
  */*) HERE_SRC="${BASH_SOURCE[0]%/*}" ;;
  *) HERE_SRC=. ;;
esac
if ! HERE="$(cd -- "${HERE_SRC:-/}" && pwd && printf x)"; then
  echo "round-preflight: cannot enter the script directory ${HERE_SRC:-/} — restore read and search access to the plugin directory, or reinstall the plugin, then re-run" >&2
  exit 2
fi
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

#: Wall-clock seconds the worktree sweep may take across every repository.
SWEEP_BUDGET_SEC=600

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

  merge_composite() { # <composite-json-file>; prints the foreman_tier status
    python3 - "$results" "$1" <<'PY'
import json, sys
path, composite = sys.argv[1:3]
with open(composite, encoding="utf-8") as handle:
    rows = json.load(handle)
if not isinstance(rows, dict) or set(rows) != {"headroom", "foreman_tier"}:
    sys.exit("the composite result is not exactly a headroom and a foreman_tier row")
merged = {}
for name, row in rows.items():
    if (not isinstance(row, dict) or not isinstance(row.get("status"), str)
            or not set(row) <= {"status", "reason", "detail"}
            or ("reason" in row and not isinstance(row["reason"], str))
            or ("detail" in row and not isinstance(row["detail"], dict))):
        sys.exit("the {} row is malformed".format(name))
    merged[name] = {**row, "due": False}
with open(path, encoding="utf-8") as handle:
    checks = json.load(handle)
checks.update(merged)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(checks, handle)
print(merged["foreman_tier"]["status"])
PY
  }

  record() { # <name> <status> <reason-or-empty> <due:0|1> <detail-file-or-empty> [command]
    # A check that could not be recorded would vanish from the aggregate and
    # let `ready` pass without it, so a failed write ends the preflight.
    record_row "$@" || die "cannot record the ${1} check in ${results}"
  }

  record_row() {
    python3 - "$results" "$1" "$2" "$3" "$4" "${5-}" "${6-}" <<'PY'
import json, sys
path, name, status, reason, due, detail, command = sys.argv[1:8]
with open(path, encoding="utf-8") as handle:
    checks = json.load(handle)
row = {"status": status, "due": due == "1"}
if reason:
    row["reason"] = reason
if detail:
    # A check whose evidence cannot be read has not passed, whatever its exit
    # code said: it blocks, and the reason names the command to re-run. The
    # detail file is scratch the EXIT trap removes, so it is never the pointer.
    try:
        with open(detail, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        row.update(status="blocked", detail=None,
                   reason="`{}` wrote output that is not readable JSON ({}); re-run "
                          "it, read its diagnostic, and repair it to emit one JSON object "
                          "before planning".format(command, exc))
    else:
        # Every collaborator emits one JSON object. Valid JSON of another shape
        # -- `[]`, `null`, a bare number -- is not that check's evidence.
        if isinstance(payload, dict):
            row["detail"] = payload
        else:
            row.update(status="blocked", detail=None,
                       reason="`{}` wrote JSON {} where its contract emits one JSON "
                              "object; re-run it, read its diagnostic, and repair it to emit "
                              "an object before planning".format(command, type(payload).__name__))
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
    record roster ok "" 0 "${scratch}/roster.json" "roster.sh"
  else
    record roster failed "roster.sh exited ${rc}; re-run it and read its diagnostic before planning" 0 ""
  fi

  # 3. Authority for this repo.
  bash "${HERE}/verify-authority.sh" "$repo" > "${scratch}/authority.json" 2>"${scratch}/authority.err"
  rc=$?
  cat "${scratch}/authority.err" >&2
  local authorized=""
  if [ "$rc" -eq 0 ]; then
    if ! authorized="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=d.get("authorized") if isinstance(d, dict) else None; print("1" if v is True else "0" if v is False else sys.exit("authorized is not a boolean"))' "${scratch}/authority.json")"; then
      authorized=""
    fi
  fi
  if [ "$rc" -eq 0 ] && [ "$authorized" = "1" ]; then
    record authority ok "" 0 "${scratch}/authority.json" "verify-authority.sh ${repo}"
  elif [ "$rc" -eq 0 ] && [ "$authorized" = "0" ]; then
    record authority denied "the operator does not own ${repo}; the round stays read-only unless the brief records per-action permission" 0 "${scratch}/authority.json" "verify-authority.sh ${repo}"
  elif [ "$rc" -eq 0 ]; then
    record authority failed "verify-authority.sh exited 0 without a readable authorized verdict for ${repo}; an unanswerable authority check is not permission" 0 ""
  else
    record authority failed "verify-authority.sh exited ${rc} for ${repo}; an unanswerable authority check is not permission" 0 ""
  fi

  # 4. Headroom and the foreman's tier: ONE composite check. The tier is
  #    selected on the headroom `foreman measure` writes, so the dependency
  #    lives inside foreman-tier-check.py, never between two preflight checks.
  #    Its single result carries a `headroom` and a `foreman_tier` row; an
  #    absent `foreman` block is the `unconfigured` warning and never blocks.
  local tier_args=("${common[@]+"${common[@]}"}" "${clock[@]+"${clock[@]}"}")
  [ "$measure" -eq 1 ] || tier_args+=(--no-measure)
  python3 "${HERE}/foreman-tier-check.py" "${tier_args[@]}" \
    > "${scratch}/foreman-tier.json" 2>"${scratch}/foreman-tier.err"
  rc=$?
  cat "${scratch}/foreman-tier.err" >&2
  if [ "$rc" -eq 0 ]; then
    local tier_status
    if ! tier_status="$(merge_composite "${scratch}/foreman-tier.json")"; then
      tier_status=""
    fi
    case "$tier_status" in
      unconfigured)
        echo "round-preflight: warning: the foreman seat is unconfigured; see checks.foreman_tier.detail.warning" >&2 ;;
      "")
        record headroom failed "foreman-tier-check.py exited 0 without one readable headroom and foreman_tier result; re-run it and read its diagnostic before planning" 0 ""
        record foreman_tier failed "foreman-tier-check.py exited 0 without one readable headroom and foreman_tier result; the foreman's tier is unproven" 0 "" ;;
    esac
  else
    record headroom failed "foreman-tier-check.py exited ${rc}; re-run it and read its diagnostic before planning" 0 ""
    record foreman_tier failed "foreman-tier-check.py exited ${rc}; the foreman's tier is unproven" 0 ""
  fi

  # 5. Capability-table cadence. Due is not blocking: the foreman refreshes it
  #    before planning, and a fleet that dispatched nothing never comes due.
  bash "${HERE}/foreman.sh" "${common[@]+"${common[@]}"}" capability-check "${clock[@]+"${clock[@]}"}" \
    > "${scratch}/capability.json" 2>"${scratch}/capability.err"
  rc=$?
  cat "${scratch}/capability.err" >&2
  if [ "$rc" -eq 0 ]; then
    local capability_due
    if capability_due="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d=d if isinstance(d, dict) else {}; print("1" if d.get("due") is True else "0" if d.get("due") is False else sys.exit("due is not a boolean"))' "${scratch}/capability.json")"; then
      record capability ok "" "$capability_due" "${scratch}/capability.json" "foreman capability-check"
    else
      record capability failed "foreman capability-check exited 0 without a readable due flag; the table's cadence is unknown" 0 ""
    fi
  else
    record capability failed "foreman capability-check exited ${rc}; the table's cadence is unknown" 0 ""
  fi

  # 6. Where this repo records its gates. Resolved once here so five workers do
  #    not each spend turns finding the same files.
  bash "${HERE}/resolve-gates.sh" "$checkout" > "${scratch}/gates.json" 2>"${scratch}/gates.err"
  rc=$?
  cat "${scratch}/gates.err" >&2
  if [ "$rc" -eq 0 ]; then
    record gates ok "" 0 "${scratch}/gates.json" "resolve-gates.sh ${checkout}"
  else
    record gates failed "resolve-gates.sh exited ${rc}; the briefs carry no gate pointers and every worker searches" 0 ""
  fi

  # 7. Worktree hygiene. Every repository with a worktree directory under
  #    the root is swept every round, before provisioning. Only this checkout's own prune
  #    blocks the round; another repository's failure is reported as degraded.
  local wroot="${WORKTREE_ROOT:-${HOME}/.worktrees}" own
  if [ ! -d "$wroot" ]; then
    record worktrees ok "" 0 ""
  else
    bash "${HERE}/bounded-run.sh" "$SWEEP_BUDGET_SEC" bash "${HERE}/sweep-worktrees.sh" "$wroot" \
      > "${scratch}/sweep.json" 2>"${scratch}/sweep.err"
    rc=$?
    cat "${scratch}/sweep.err" >&2
    case "$rc" in
      0) record worktrees ok "" 0 "${scratch}/sweep.json" "sweep-worktrees.sh ${wroot}" ;;
      1) record worktrees undecided "sweep-worktrees.sh decided nothing; fix its diagnostic and re-run before provisioning" 0 "" ;;
      2)
        # Whose failure it is: this checkout's prune (undecided / failed), an
        # error no repository could be named for (unassociated), or another
        # repository's alone (degraded). Only the last lets the round proceed.
        if ! own="$(python3 - "${scratch}/sweep.json" "$checkout" <<'PY'
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    sweep = json.load(handle)
mine = os.path.realpath(sys.argv[2])
entry = next((r for r in sweep["repos"] if os.path.realpath(r["shared"]) == mine), None)
errors = sweep.get("errors", [])
if entry is not None and entry["exit"] == 1:
    print("undecided")
elif (entry is not None and (entry["exit"] != 0 or "result" not in entry)) \
        or any(e.get("repo") and os.path.realpath(e["repo"]) == mine for e in errors):
    # A result that could not be read is a failure, whatever the exit said.
    print("failed")
elif any(not e.get("repo") for e in errors):
    print("unassociated")
else:
    print("degraded")
PY
)"; then
          record worktrees failed "sweep-worktrees.sh exited 2 and its JSON could not be read" 0 ""
        else
          case "$own" in
            undecided) record worktrees undecided "prune-worktrees.sh decided nothing for ${checkout}; fix its diagnostic and re-run before provisioning" 0 "${scratch}/sweep.json" "sweep-worktrees.sh ${wroot}" ;;
            failed) record worktrees failed "the sweep reported a failure for ${checkout}; git refused a check or a removal" 0 "${scratch}/sweep.json" "sweep-worktrees.sh ${wroot}" ;;
            unassociated) record worktrees failed "the sweep could not read a worktree under ${wroot} and could not name its repository; inspect the errors entry before provisioning" 0 "${scratch}/sweep.json" "sweep-worktrees.sh ${wroot}" ;;
            *) record worktrees degraded "" 0 "${scratch}/sweep.json" "sweep-worktrees.sh ${wroot}" ;;
          esac
        fi ;;
      124) record worktrees failed "sweep-worktrees.sh ran past its ${SWEEP_BUDGET_SEC}s budget and was stopped; run it by hand to see which repository's origin it waits on" 0 "" ;;
      *) record worktrees failed "sweep-worktrees.sh exited ${rc}" 0 "" ;;
    esac
  fi

  local payload
  payload="$(emit "$results")" || die "cannot assemble the preflight payload"
  printf '%s\n' "$payload"
  printf '%s' "$payload" \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["ready"] else 1)'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
