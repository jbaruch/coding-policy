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
#        `self` carries path, branch, staged/unstaged/untracked counts, and the
#        same tip_in_main/age_hours/verdict the other entries carry. The release
#        gate blocks on any `self` dirt whatever its verdict; the verdict is
#        there for a caller that only wants the abandoned shape, such as the
#        session-start hook, which has no reason to nag about work in progress.
# Exit:  0 nothing blocks the release; 1 leftovers block it, each named in
#        `blocking` with the diagnostic on stderr; 2 usage or tool error (not a
#        git repository, git absent, unreadable worktree list, a worktree whose
#        status or ancestry git cannot read). A tool error is never a verdict:
#        the script refuses to answer rather than answering "clean".
#
# LEFTOVERS_MIN_AGE_HOURS overrides the age floor an OTHER worktree must clear
# before its dirt reads as abandoned rather than freshly started.
set -euo pipefail

#: An other-worktree leftover younger than this is someone still typing.
LEFTOVERS_MIN_AGE_HOURS="${LEFTOVERS_MIN_AGE_HOURS:-4}"

#: Where git's stderr lands while its stdout is carrying NUL-separated records.
ERR_SINK=/dev/null
#: The directory holding it. Global, not a local of main(): the EXIT trap runs
#: after main() returns, when a local would already be unset.
SCRATCH=""

json_str() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"
  printf '"%s"' "$s"
}

die() { echo "check-leftovers: $*" >&2; printf '{"ok":false,"self":null,"others":[],"blocking":[]}\n'; exit 2; }

# `return 0` so a failed removal cannot rewrite the verdict this script exits
# with (rules/error-handling.md Shell Error Handling).
cleanup() {
  [ -z "$SCRATCH" ] || rm -rf "$SCRATCH"
  return 0
}

# Seconds since a path was last written. GNU `stat` is probed first: BSD's `-f`
# is GNU's --file-system, which answers an unrelated question and SUCCEEDS while
# doing it, so probing the other way round yields a non-time on Linux and every
# age reads as zero. A result that is not a whole number of seconds warns and
# reads as just-written, which under-reports an age rather than aging a path
# into a refusal on a bad read.
age_seconds() {
  local path="$1" now mtime
  now="$(date +%s)"
  mtime="$(stat -c %Y "$path" 2>/dev/null)" || mtime=""
  [ -n "$mtime" ] || mtime="$(stat -f %m "$path" 2>/dev/null)" || mtime=""
  case "$mtime" in
    ''|*[!0-9]*)
      echo "check-leftovers: cannot read the modification time of ${path} -- treating it as just written, so its age alone will not block; check the path is readable" >&2
      echo 0
      return 0
      ;;
  esac
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

# Every `git status --porcelain` record for a worktree, one per line. `-z` keeps
# a path with a space or a newline in one field. Exits non-zero, naming git's own
# message, when git could not read the worktree -- an unreadable worktree and a
# clean one are opposite answers and must not collapse into the same zero.
#
# `tr` runs INSIDE the substitution, not after it: command substitution discards
# NUL bytes, so capturing first would fuse every record into one line. Under
# `pipefail` git's failure still reaches $?, and its stderr goes to a file
# because the pipe is already carrying the records.
porcelain_lines() { # <worktree>
  local out status=0
  out="$(git -C "$1" status --porcelain -z --untracked-files=normal 2>"$ERR_SINK" | tr '\0' '\n')" || status=$?
  if [ "$status" -ne 0 ]; then
    echo "check-leftovers: cannot read git status in ${1} (exit ${status}): $(cat "$ERR_SINK")" >&2
    return 1
  fi
  [ -z "$out" ] || printf '%s\n' "$out"
}

# The changed paths alone: the porcelain record minus its two status characters
# and the space after them.
changed_paths() { # <worktree>
  porcelain_lines "$1" | sed -n 's/^.\{3\}//p'
}

# How many records match a porcelain status pattern. grep exits 1 on no match
# and 2 on a real error, so only 1 is read as none.
count_matching() { # <worktree> <status-regex>
  local lines n status=0
  lines="$(porcelain_lines "$1")" || return 1
  n="$(printf '%s\n' "$lines" | grep -c "$2")" || status=$?
  if [ "$status" -gt 1 ]; then
    echo "check-leftovers: grep failed reading ${1}'s status (exit ${status})" >&2
    return 1
  fi
  echo "$n"
}

# true when <rev> is already an ancestor of <base>. git answers 0 for yes and 1
# for no; anything above that is a real failure (a bad object, an unreadable
# worktree) and must not read as "carries its own commits", which is the answer
# that spares a worktree from the verdict.
tip_is_in() { # <worktree> <rev> <base>
  local err status=0
  err="$(git -C "$1" merge-base --is-ancestor "$2" "$3" 2>&1)" || status=$?
  case "$status" in
    0) echo true ;;
    1) echo false ;;
    *) echo "check-leftovers: cannot tell whether ${2} is already in ${3} inside ${1} (exit ${status}): ${err}" >&2
       return 1 ;;
  esac
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

  SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/check-leftovers.XXXXXX")" \
    || die "cannot create a temporary directory under ${TMPDIR:-/tmp} -- check it is writable, then re-run"
  trap cleanup EXIT
  ERR_SINK="${SCRATCH}/git-stderr"
  : > "$ERR_SINK" || die "cannot write to ${ERR_SINK} -- check ${TMPDIR:-/tmp} is writable, then re-run"
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

  local self_branch staged unstaged untracked self_tip_in_main self_age self_verdict
  self_branch="$(git -C "$self_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")"
  staged="$(count_matching "$self_path" '^[MADRC]')" \
    || die "cannot count staged paths in ${self_path} -- see the diagnostic above"
  unstaged="$(count_matching "$self_path" '^.[MD]')" \
    || die "cannot count unstaged paths in ${self_path} -- see the diagnostic above"
  untracked="$(count_matching "$self_path" '^??')" \
    || die "cannot count untracked paths in ${self_path} -- see the diagnostic above"
  self_tip_in_main="$(tip_is_in "$self_path" HEAD "$base")" \
    || die "cannot classify ${self_path} against ${base} -- see the diagnostic above"
  self_age="$(dirt_age_hours "$self_path")"
  self_verdict="clean"
  if [ $(( staged + unstaged + untracked )) -gt 0 ]; then
    self_verdict="in_progress"
    if [ "$self_tip_in_main" = true ] && [ "$self_age" -ge "$LEFTOVERS_MIN_AGE_HOURS" ]; then
      self_verdict="abandoned"
    fi
  fi
  if [ $(( staged + unstaged + untracked )) -gt 0 ]; then
    ok=false
    blocking+=("$(printf 'this worktree (%s) has %d staged, %d unstaged and %d untracked path(s); a release publishes only what is committed' \
      "$self_branch" "$staged" "$unstaged" "$untracked")")
  fi

  local worktree_list
  worktree_list="$(git -C "$repo" worktree list --porcelain)" \
    || die "cannot read the worktree list for ${repo} -- run 'git -C ${repo} worktree list' to see why"

  local wt branch tip_in_main age verdict dirt
  while IFS= read -r wt; do
    [ -n "$wt" ] || continue
    [ "$wt" = "$self_path" ] && continue
    [ -d "$wt" ] || continue
    dirt="$(changed_paths "$wt")" \
      || die "cannot read the working tree at ${wt} -- see the diagnostic above"
    [ -n "$dirt" ] || continue

    branch="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")"
    tip_in_main="$(tip_is_in "$wt" HEAD "$base")" \
      || die "cannot classify ${wt} against ${base} -- see the diagnostic above"
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
  done < <(printf '%s\n' "$worktree_list" | sed -n 's/^worktree //p')

  local others_out="" blocking_out="" item
  for item in ${others_json+"${others_json[@]}"}; do
    others_out="${others_out:+${others_out},}${item}"
  done
  for item in ${blocking+"${blocking[@]}"}; do
    blocking_out="${blocking_out:+${blocking_out},}$(json_str "$item")"
  done

  printf '{"ok":%s,"self":{"path":%s,"branch":%s,"staged":%d,"unstaged":%d,"untracked":%d,"tip_in_main":%s,"age_hours":%d,"verdict":%s},"others":[%s],"blocking":[%s]}\n' \
    "$ok" "$(json_str "$self_path")" "$(json_str "$self_branch")" \
    "$staged" "$unstaged" "$untracked" "$self_tip_in_main" "$self_age" \
    "$(json_str "$self_verdict")" "$others_out" "$blocking_out"

  if [ "$ok" = false ]; then
    echo "check-leftovers: the release cannot start -- uncommitted work would be left behind:" >&2
    for item in ${blocking+"${blocking[@]}"}; do echo "  - ${item}" >&2; done
    echo "Commit it, stash it, or add it to .gitignore, then re-run. Inspect a named worktree with 'git -C <path> status'." >&2
    exit 1
  fi
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
