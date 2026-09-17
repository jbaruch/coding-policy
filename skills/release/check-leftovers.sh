#!/usr/bin/env bash
# Refuse to start a release while work sits uncommitted in a worktree.
#
# The release flow's later steps are all scripted; Step 1 was prose, so the
# decision to OPEN a PR rested on the agent remembering to look. It did not:
# jbaruch/coding-policy's pane-label change was written across four files,
# never committed, and sat in `~/.worktrees/coding-policy-pane-model` for nine
# days while its branch reported as merged -- the branch tip was a plain `main`
# commit, so every "delete merged branches" heuristic called the worktree
# disposable while the only copy of the work lived beside it, untracked.
#
# Two verdicts, because the two shapes need different strictness:
#
#   SELF -- the worktree the release runs from. Any staged, unstaged or
#   untracked path blocks. A release publishes what is committed, so anything
#   else is either work about to be lost or junk that belongs in .gitignore,
#   and the caller must say which.
#
#   OTHER -- every other worktree sharing this repository. Concurrent agents
#   legitimately hold work in progress here (rules/agent-worktree-isolation.md),
#   so dirt alone proves nothing. What proves abandonment is dirt on a branch
#   whose tip is already an ANCESTOR of origin/main: nothing was ever committed
#   on it. A branch carrying its own commits is recoverable from git and is left
#   alone, and a branch whose tip is in main cannot carry an open pull request
#   either -- GitHub has no commits to show -- which is why this needs no `gh`
#   and no network. An age floor keeps a worktree created minutes ago from
#   tripping it.
#
# Usage: check-leftovers.sh [--repo <path>]
# Out:   one JSON object on stdout, on every exit code below:
#          {"ok":bool,"self":{...},"others":[{...}],"blocking":["<reason>",...]}
#        `self` carries path, branch, staged/unstaged/untracked counts.
#        Each `others` entry carries path, branch, tip_in_main, age_hours and a
#        verdict of "abandoned" (blocks) or "in_progress" (does not).
# Exit:  0 nothing blocks the release; 1 leftovers block it, each named in
#        `blocking` with the diagnostic on stderr; 2 usage or tool error
#        (not a git repository, git absent, unreadable worktree list).
#
# LEFTOVERS_MIN_AGE_HOURS overrides the age floor an OTHER worktree must clear
# before its dirt reads as abandoned rather than freshly started.
set -euo pipefail

#: An other-worktree leftover younger than this is someone still typing.
LEFTOVERS_MIN_AGE_HOURS="${LEFTOVERS_MIN_AGE_HOURS:-4}"

json_str() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"
  printf '"%s"' "$s"
}

die() { echo "check-leftovers: $*" >&2; printf '{"ok":false,"self":null,"others":[],"blocking":[]}\n'; exit 2; }

# Seconds since a path was last written, or 0 when it cannot be read: an
# unreadable path is reported as brand new rather than aged into a refusal.
age_seconds() {
  local path="$1" now mtime
  now="$(date +%s)"
  mtime="$(stat -f %m "$path" 2>/dev/null || stat -c %Y "$path" 2>/dev/null || echo "$now")"
  echo $(( now - mtime ))
}

# The newest write among a worktree's changed paths, in whole hours.
dirt_age_hours() {
  local wt="$1" newest=999999999 rel age
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    age="$(age_seconds "${wt}/${rel}")"
    [ "$age" -lt "$newest" ] && newest="$age"
  done < <(changed_paths "$wt")
  [ "$newest" -eq 999999999 ] && newest=0
  echo $(( newest / 3600 ))
}

# Every path git reports as changed: staged, unstaged, or untracked and not
# ignored. `-z` keeps a path with a space or a newline in one field.
changed_paths() {
  git -C "$1" status --porcelain -z --untracked-files=normal 2>/dev/null \
    | tr '\0' '\n' | sed -n 's/^.\{3\}//p'
}

count_matching() {
  git -C "$1" status --porcelain -z --untracked-files=normal 2>/dev/null \
    | tr '\0' '\n' | grep -c "$2" || true
}

main() {
  local repo="."
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) [ "$#" -ge 2 ] || die "--repo needs a path"; repo="$2"; shift 2 ;;
      *) echo "usage: check-leftovers.sh [--repo <path>]" >&2
         printf '{"ok":false,"self":null,"others":[],"blocking":[]}\n'; exit 2 ;;
    esac
  done

  command -v git >/dev/null || die "git not found on PATH -- install it, then re-run"
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 \
    || die "${repo} is not a git repository -- run this from the worktree you are releasing from"

  local self_path
  self_path="$(git -C "$repo" rev-parse --show-toplevel)" \
    || die "cannot resolve the worktree root of ${repo}"

  local base="origin/main"
  git -C "$repo" rev-parse --verify --quiet "$base" >/dev/null || base="main"
  git -C "$repo" rev-parse --verify --quiet "$base" >/dev/null \
    || die "neither origin/main nor main resolves -- fetch the remote, then re-run"

  local blocking=() others_json=() ok=true

  local self_branch staged unstaged untracked
  self_branch="$(git -C "$self_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")"
  staged="$(count_matching "$self_path" '^[MADRC]')"
  unstaged="$(count_matching "$self_path" '^.[MD]')"
  untracked="$(count_matching "$self_path" '^??')"
  if [ $(( staged + unstaged + untracked )) -gt 0 ]; then
    ok=false
    blocking+=("$(printf 'this worktree (%s) has %d staged, %d unstaged and %d untracked path(s); a release publishes only what is committed' \
      "$self_branch" "$staged" "$unstaged" "$untracked")")
  fi

  local wt branch tip_in_main age verdict
  while IFS= read -r wt; do
    [ -n "$wt" ] || continue
    [ "$wt" = "$self_path" ] && continue
    [ -d "$wt" ] || continue
    [ -n "$(changed_paths "$wt")" ] || continue

    branch="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")"
    tip_in_main=false
    if git -C "$wt" merge-base --is-ancestor HEAD "$base" 2>/dev/null; then tip_in_main=true; fi
    age="$(dirt_age_hours "$wt")"

    verdict="in_progress"
    if [ "$tip_in_main" = true ] && [ "$age" -ge "$LEFTOVERS_MIN_AGE_HOURS" ]; then
      verdict="abandoned"
      ok=false
      blocking+=("$(printf '%s holds uncommitted work on %s, a branch carrying no commits of its own, last written %dh ago; nothing in git preserves it' \
        "$wt" "$branch" "$age")")
    fi
    others_json+=("$(printf '{"path":%s,"branch":%s,"tip_in_main":%s,"age_hours":%d,"verdict":%s}' \
      "$(json_str "$wt")" "$(json_str "$branch")" "$tip_in_main" "$age" "$(json_str "$verdict")")")
  done < <(git -C "$repo" worktree list --porcelain | sed -n 's/^worktree //p')

  local others_out="" blocking_out="" item
  for item in ${others_json+"${others_json[@]}"}; do
    others_out="${others_out:+${others_out},}${item}"
  done
  for item in ${blocking+"${blocking[@]}"}; do
    blocking_out="${blocking_out:+${blocking_out},}$(json_str "$item")"
  done

  printf '{"ok":%s,"self":{"path":%s,"branch":%s,"staged":%d,"unstaged":%d,"untracked":%d},"others":[%s],"blocking":[%s]}\n' \
    "$ok" "$(json_str "$self_path")" "$(json_str "$self_branch")" \
    "$staged" "$unstaged" "$untracked" "$others_out" "$blocking_out"

  if [ "$ok" = false ]; then
    echo "check-leftovers: the release cannot start -- uncommitted work would be left behind:" >&2
    for item in ${blocking+"${blocking[@]}"}; do echo "  - ${item}" >&2; done
    echo "Commit it, stash it, or add it to .gitignore, then re-run. Inspect a named worktree with 'git -C <path> status'." >&2
    exit 1
  fi
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
