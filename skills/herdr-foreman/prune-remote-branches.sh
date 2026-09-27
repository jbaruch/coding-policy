#!/usr/bin/env bash
# Delete the spent branches on origin; report the ones holding work that no
# open pull request carries, never touch them.
#
# `prune-worktrees.sh` owns a repository's worktrees and local branches; this
# script owns its branches on origin, a separate concern (network, the GitHub
# CLI). Which branch is safe to delete is one right answer per input, so the
# decision lives here (`rules/script-delegation.md`).
#
# Decision predicate — a branch on origin, other than origin's default branch,
# is:
#   * NEVER TOUCHED OR LISTED when it is protected, or an open pull request
#     has it as its head (`gh pr list --state open`);
#   * DELETED when merged into origin's default branch: immediately before the
#     deletion (and before a dry run's preview), the branch's tip, the default
#     branch's tip, which branch origin's HEAD names, the open pull requests
#     for it and its protection are read again, and any change keeps it
#     (changed). The deletion is `git push origin --delete` with
#     `--force-with-lease=<branch>:<tip>`, so a push that landed since wins;
#   * QUESTIONABLE, reported for the operator's decision, when unmerged, its
#     tip commit is at least REMOTE_IDLE_HOURS old, and the same final
#     re-read of origin finds nothing changed: commits ahead of the
#     default branch, age, last author, and the commands to open a pull
#     request or delete it, the delete leased to the tip judged here;
#   * KEPT silently when unmerged and younger than that (not-idle).
# Without the GitHub CLI, or when it fails, nothing is deleted or listed:
# `could_not_check` says why. A failed git or gh command that talks to origin
# is reported by exit code and the command to rerun, never by its own
# message, which can carry the remote URL with credentials.
#
# Contract:
#   argv  : <shared-checkout> [--dry-run]
#           --dry-run reports the same decisions and deletes nothing. It
#           still fetches, so its answer is current.
#   stdout: one JSON object —
#           {"shared":"<abs>","default_branch":"<name>","dry_run":bool,
#            "deleted":["<branch>"],
#            "questionable":[{"branch","ahead","age_hours","author",
#                             "open_pr","delete"}],
#            "kept":[{"branch","reason"}],
#            "could_not_check":"<why>"|null,
#            "failed":[{"target","error"}]}
#           kept reasons: changed (its tip, the default branch or its tip,
#           its protection, or its open pull requests changed since it was
#           judged, for a deletion or a listing alike), not-idle.
#   stderr: diagnostics only.
#   exit  : 0 every decision applied (or previewed),
#           1 precondition unmet (usage, git or python3 absent, not a repo,
#             no origin, fetch or ls-remote failed) — no JSON,
#           2 could_not_check is set or `failed` is non-empty.
#   env   : PRUNE_REMOTE_IDLE_HOURS overrides REMOTE_IDLE_HOURS, PRUNE_NOW
#           (epoch seconds) the clock; the tests point PATH at a fake gh.
set -euo pipefail

#: An unmerged branch is reported only once its tip commit is this old.
REMOTE_IDLE_HOURS="${PRUNE_REMOTE_IDLE_HOURS:-24}"

WORKDIR=""
ERRFILE=""
ROWS=""

warn() { printf 'prune-remote-branches: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$WORKDIR" ]] && ! rm -rf "$WORKDIR"; then
    warn "could not remove the temporary directory ${WORKDIR} — remove it by hand"
  fi
  return 0
}

# One NUL-delimited decision row: <kind> <branch> <a> <b> <c> <d>.
row() {
  printf '%s\0%s\0%s\0%s\0%s\0%s\0' "$1" "$2" "${3:-}" "${4:-}" "${5:-}" "${6:-}" >> "$ROWS"
  if [[ "$1" == failed ]]; then warn "${2}: ${3} — inspect it by hand; nothing else was skipped on its account"; fi
}

# A command that talks to origin failed: keep only its exit code and the
# command to rerun; its own message can carry the remote URL with credentials.
network_failure() { # <exit> <dir> <command...>
  local rc="$1" dir="$2"
  shift 2
  # shellcheck disable=SC2016  # The backticks are literal text in the message.
  printf '`%s` exited %s; run it in %s to see why (its output is not relayed: it can carry the remote URL with credentials)\n' \
    "$*" "$rc" "$dir" > "$ERRFILE"
}

# Echo "<sha>" origin holds for refs/heads/<branch> now, empty when none.
remote_tip() { # <shared> <branch>
  local out rc=0
  out="$(git -C "$1" ls-remote origin "refs/heads/$2" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$1" git ls-remote origin "refs/heads/$2"; return 1; fi
  printf '%s' "${out%%[[:space:]]*}"
}

# Echo the open pull requests' head branch names, one per line.
open_pr_heads() { # <shared> [branch]
  local rc=0 out
  local -a args=(pr list --state open --limit 1000 --json headRefName --jq '.[].headRefName')
  [[ -n "${2:-}" ]] && args+=(--head "$2")
  out="$(cd "$1" && GH_PROMPT_DISABLED=1 gh "${args[@]}" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$1" gh "${args[@]}"; return 1; fi
  printf '%s' "$out"
}

# Echo the branch origin's HEAD names now.
remote_default() { # <shared>
  local out rc=0 name
  out="$(git -C "$1" ls-remote --symref origin HEAD 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$1" git ls-remote --symref origin HEAD; return 1; fi
  name="$(printf '%s\n' "$out" | sed -n 's#^ref: refs/heads/\(.*\)[[:space:]]HEAD$#\1#p' | head -n 1)"
  if [[ -z "$name" ]]; then printf 'origin reports no default branch\n' > "$ERRFILE"; return 1; fi
  printf '%s' "$name"
}

# Echo "true" or "false": whether GitHub protects <branch> now.
branch_protected() { # <shared> <branch>
  local out rc=0 segment
  # One path segment: a branch named feat/add-auth would otherwise route as
  # two, and the lookup would fail for every such branch.
  segment="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$2")"
  out="$(cd "$1" && GH_PROMPT_DISABLED=1 gh api "repos/{owner}/{repo}/branches/${segment}" --jq .protected 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$1" gh api "repos/{owner}/{repo}/branches/${segment}"; return 1; fi
  printf '%s' "$out"
}

main() {
  local shared="" dry=0 arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry=1 ;;
      -*) warn "unknown flag '${arg}' — usage: prune-remote-branches.sh <shared-checkout> [--dry-run]"; return 1 ;;
      *) if [[ -n "$shared" ]]; then warn "usage: prune-remote-branches.sh <shared-checkout> [--dry-run]"; return 1; fi; shared="$arg" ;;
    esac
  done
  if [[ -z "$shared" ]]; then warn "usage: prune-remote-branches.sh <shared-checkout> [--dry-run]"; return 1; fi
  local tool
  for tool in git python3; do
    if ! command -v "$tool" >/dev/null; then warn "${tool} not found on PATH — install it"; return 1; fi
  done
  if ! WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/prune-remote-branches.XXXXXX")"; then
    WORKDIR=""
    warn "cannot create a temporary directory under ${TMPDIR:-/tmp} — make it writable, then re-run"
    return 1
  fi
  trap cleanup EXIT
  ERRFILE="${WORKDIR}/err"; ROWS="${WORKDIR}/rows"
  : > "$ERRFILE"; : > "$ROWS"
  if [[ ! -d "$shared" ]] || ! git -C "$shared" rev-parse --is-inside-work-tree >/dev/null 2>"$ERRFILE"; then
    warn "'${shared}' is not a git work tree ($(tr '\n' ' ' < "$ERRFILE")) — pass the shared checkout's path"; return 1
  fi
  # A sentinel past git's own newline: command substitution strips both, and a
  # path ending in a newline would lose its own.
  shared="$(git -C "$shared" rev-parse --show-toplevel && printf x)"
  shared="${shared%x}"; shared="${shared%$'\n'}"
  if ! git -C "$shared" remote get-url origin >/dev/null 2>"$ERRFILE"; then
    warn "${shared} has no origin remote ($(tr '\n' ' ' < "$ERRFILE")) — nothing to judge; add one with \`git -C ${shared} remote add origin <url>\`, then re-run"; return 1
  fi
  # Origin's branches are listed before the fetch, so the fetch brings in
  # every commit the listing names.
  local rc=0 sym db heads
  rc=0; sym="$(git -C "$shared" ls-remote --symref origin HEAD 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$shared" git ls-remote --symref origin HEAD; warn "$(cat "$ERRFILE")"; return 1; fi
  db="$(printf '%s\n' "$sym" | sed -n 's#^ref: refs/heads/\(.*\)[[:space:]]HEAD$#\1#p' | head -n 1)"
  if [[ -z "$db" ]]; then warn "origin reports no default branch — run \`git ls-remote --symref origin HEAD\` in ${shared}"; return 1; fi
  rc=0; heads="$(git -C "$shared" ls-remote --heads origin 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$shared" git ls-remote --heads origin; warn "$(cat "$ERRFILE")"; return 1; fi
  local -a fetch_args=(fetch --quiet origin)
  (( dry )) || fetch_args=(fetch --quiet --prune origin)
  rc=0; git -C "$shared" "${fetch_args[@]}" 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then network_failure "$rc" "$shared" git "${fetch_args[@]}"; warn "$(cat "$ERRFILE")"; return 1; fi

  local could_not_check="" protected="" prs=""
  if ! command -v gh >/dev/null; then
    could_not_check="the GitHub CLI (gh) is not on PATH, so open pull requests and protected branches cannot be checked — install gh and run \`gh auth login\`"
  else
    rc=0
    protected="$(cd "$shared" && GH_PROMPT_DISABLED=1 gh api 'repos/{owner}/{repo}/branches?protected=true&per_page=100' --paginate --jq '.[].name' 2>"$ERRFILE")" || rc=$?
    if (( rc != 0 )); then
      network_failure "$rc" "$shared" gh api 'repos/{owner}/{repo}/branches?protected=true'
      could_not_check="$(cat "$ERRFILE")"
    elif ! prs="$(open_pr_heads "$shared")"; then
      could_not_check="$(cat "$ERRFILE")"
    fi
  fi

  if [[ -z "$could_not_check" ]]; then
    local default_tip line branch tip
    default_tip="$(printf '%s\n' "$heads" | awk -v r="refs/heads/$db" '$2 == r {print $1}')"
    while IFS=$'\t' read -r tip line; do
      [[ -n "$tip" ]] || continue
      branch="${line#refs/heads/}"
      [[ "$branch" == "$db" ]] && continue
      grep -qxF "$branch" <<<"$protected" && continue
      grep -qxF "$branch" <<<"$prs" && continue
      decide_remote "$shared" "$db" "$default_tip" "$branch" "$tip" "$dry"
    done <<<"$heads"
  fi

  rc=0
  python3 - "$shared" "$db" "$dry" "$ROWS" "$could_not_check" <<'PY' || rc=$?
import json, shlex, sys
shared, db, dry, rows_path, cannot = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4], sys.argv[5]
out = {"shared": shared, "default_branch": db, "dry_run": dry, "deleted": [], "questionable": [], "kept": [],
       "could_not_check": cannot or None, "failed": []}
with open(rows_path, "rb") as handle:
    fields = handle.read().decode("utf-8", "surrogateescape").split("\0")
if fields and fields[-1] == "":
    fields.pop()
for i in range(0, len(fields), 6):
    kind, branch, a, b, c, d = fields[i:i + 6]
    if kind == "deleted":
        out["deleted"].append(branch)
    elif kind == "kept":
        out["kept"].append({"branch": branch, "reason": a})
    elif kind == "questionable":
        where = shlex.quote(shared)
        out["questionable"].append({"branch": branch, "ahead": int(a), "age_hours": int(b), "author": c,
                                    "open_pr": "gh pr create --head {}".format(shlex.quote(branch)),
                                    "delete": "git -C {} push --force-with-lease={} origin --delete {}".format(
                                        where, shlex.quote("refs/heads/{}:{}".format(branch, d)),
                                        shlex.quote("refs/heads/" + branch))})
    else:
        out["failed"].append({"target": branch, "error": a})
print(json.dumps(out, sort_keys=True))
sys.exit(2 if out["failed"] or out["could_not_check"] else 0)
PY
  return "$rc"
}

# The final gate, before a deletion (or its preview) and before a listing:
# origin as it is now. 0 when the branch's tip, the default branch's name,
# its open pull requests and its protection are as judged, and the default's
# tip still settles the verdict (a merged tip still contained; an unmerged
# one against an unmoved default). 1 when anything changed, 2 when a read
# failed (ERRFILE says why).
origin_gate() { # <shared> <default> <default-tip> <branch> <tip> <merged|unmerged>
  local shared="$1" db="$2" default_tip="$3" branch="$4" tip="$5" verdict="$6"
  local now_tip now_default now_prs now_protected now_db mrc=0
  if ! now_tip="$(remote_tip "$shared" "$branch")" || ! now_default="$(remote_tip "$shared" "$db")" \
    || ! now_prs="$(open_pr_heads "$shared" "$branch")" || ! now_protected="$(branch_protected "$shared" "$branch")" \
    || ! now_db="$(remote_default "$shared")"; then
    return 2
  fi
  if [[ "$now_tip" != "$tip" || -n "$now_prs" || "$now_protected" != false || "$now_db" != "$db" ]]; then
    return 1
  fi
  [[ "$now_default" == "$default_tip" ]] && return 0
  [[ "$verdict" == merged ]] || return 1
  git -C "$shared" merge-base --is-ancestor "$tip" "$now_default" 2>"$ERRFILE" || mrc=$?
  (( mrc == 0 ))
}

# Decide one branch on origin; delete it (unless dry-run) or record why not.
decide_remote() { # <shared> <default> <default-tip> <branch> <tip> <dry 0|1>
  local shared="$1" db="$2" default_tip="$3" branch="$4" tip="$5" dry="$6" rc=0
  git -C "$shared" merge-base --is-ancestor "$tip" "$default_tip" 2>"$ERRFILE" || rc=$?
  case "$rc" in
    0)
      rc=0; origin_gate "$shared" "$db" "$default_tip" "$branch" "$tip" merged || rc=$?
      case "$rc" in
        0) ;;
        1) row kept "$branch" changed; return 0 ;;
        *) row failed "$branch" "cannot re-read origin before deleting it, so it was kept: $(cat "$ERRFILE")"; return 0 ;;
      esac
      if (( dry )); then row deleted "$branch"; return 0; fi
      rc=0
      git -C "$shared" push --quiet "--force-with-lease=refs/heads/${branch}:${tip}" origin --delete "refs/heads/${branch}" 2>"$ERRFILE" || rc=$?
      if (( rc != 0 )); then
        # A lease refused because the branch moved is the safety working, not
        # a failure: origin's tip, read again, tells the two apart.
        local after_tip
        if after_tip="$(remote_tip "$shared" "$branch")" && [[ "$after_tip" != "$tip" ]]; then
          row kept "$branch" changed; return 0
        fi
        network_failure "$rc" "$shared" git push "--force-with-lease=refs/heads/${branch}:${tip}" origin --delete "refs/heads/${branch}"
        row failed "$branch" "deleting it on origin failed: $(cat "$ERRFILE")"; return 0
      fi
      row deleted "$branch" ;;
    1)
      local committed author age ahead
      if ! committed="$(git -C "$shared" log -1 --format=%ct "$tip" 2>"$ERRFILE")" \
        || ! author="$(git -C "$shared" log -1 --format=%an "$tip" 2>"$ERRFILE")" \
        || ! ahead="$(git -C "$shared" rev-list --count "${default_tip}..${tip}" 2>"$ERRFILE")"; then
        row failed "$branch" "cannot read its history: $(tr '\n' ' ' < "$ERRFILE")"; return 0
      fi
      age="$(python3 -c 'import sys, time; now = float(sys.argv[1]) if sys.argv[1] else time.time(); print(int(max(0.0, now - int(sys.argv[2])) // 3600))' "${PRUNE_NOW:-}" "$committed")"
      if (( age < REMOTE_IDLE_HOURS )); then row kept "$branch" not-idle; return 0; fi
      rc=0; origin_gate "$shared" "$db" "$default_tip" "$branch" "$tip" unmerged || rc=$?
      case "$rc" in
        0) row questionable "$branch" "$ahead" "$age" "$author" "$tip" ;;
        1) row kept "$branch" changed ;;
        *) row failed "$branch" "cannot re-read origin before listing it, so it was kept: $(cat "$ERRFILE")" ;;
      esac ;;
    *) row failed "$branch" "git merge-base failed: $(tr '\n' ' ' < "$ERRFILE")" ;;
  esac
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
