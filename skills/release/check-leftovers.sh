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
# Every read here fails closed. A gate that answers "clean" when it could not
# look is worse than one that refuses to answer, so a git command that fails,
# a clock that cannot be read and a path whose status cannot be parsed all exit
# 2 rather than producing a verdict.
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
#        git repository, git absent, an unreadable worktree list or status, an
#        unreadable clock, an unreadable modification time, a registered
#        worktree that is not a readable directory, a base ref that exists but
#        does not resolve).
#
# LEFTOVERS_MIN_AGE_HOURS overrides the age floor an OTHER worktree must clear
# before its dirt reads as abandoned rather than freshly started. A value that is
# not a whole number of hours exits 2 rather than silently failing every
# comparison it is used in.
set -euo pipefail

#: An other-worktree leftover younger than this is someone still typing.
LEFTOVERS_MIN_AGE_HOURS="${LEFTOVERS_MIN_AGE_HOURS:-4}"

#: Where a git command's stderr lands while its stdout carries NUL-separated
#: records. Set once the scratch directory exists.
ERR_SINK=/dev/null
#: That directory. Global, not a local of main(): the EXIT trap runs after
#: main() returns, when a local would already be unset. The trap names this
#: function rather than an interpolated path, so nothing in TMPDIR reaches the
#: trap as shell source.
SCRATCH=""

#: read_status() fills these. Arrays and counters rather than a parsed string:
#: a path may contain a newline, which no line-oriented carrier survives.
WT_PATHS=()
WT_STAGED=0
WT_UNSTAGED=0
WT_UNTRACKED=0

json_str() {
  local s="$1" out="" i ch esc
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"
  # Every remaining C0 control character is legal in a path and illegal raw in
  # a JSON string, so each becomes its \u escape. Raw, one of them makes the
  # whole envelope unparseable -- the shape every reader of this script
  # depends on (rules/script-delegation.md Script Requirements).
  case "$s" in
    *[[:cntrl:]]*) ;;
    *) printf '"%s"' "$s"; return 0 ;;
  esac
  for (( i = 0; i < ${#s}; i++ )); do
    ch="${s:i:1}"
    case "$ch" in
      [[:cntrl:]]) printf -v esc '\\u%04x' "'$ch"; out+="$esc" ;;
      *) out+="$ch" ;;
    esac
  done
  printf '"%s"' "$out"
}

die() { echo "check-leftovers: $*" >&2; printf '{"ok":false,"self":null,"others":[],"blocking":[]}\n'; exit 2; }

# The removal is checked explicitly, not suppressed, and `return 0` is last:
# under `set -e` a failing `rm` would abort the handler before it got there and
# replace the verdict this script exits with (rules/error-handling.md Shell
# Error Handling).
cleanup() {
  if [ -n "$SCRATCH" ] && ! rm -rf "$SCRATCH"; then
    echo "check-leftovers: could not remove the temporary directory ${SCRATCH} -- delete it by hand" >&2
  fi
  return 0
}

# Seconds since a path was last written. GNU `stat` is probed first: BSD's `-f`
# is GNU's --file-system, which answers an unrelated question and SUCCEEDS while
# doing it, so probing the other way round yields a non-time on Linux and every
# age reads as zero.
#
# A path that no longer exists is a deletion, and deleting it is exactly what
# updated its parent directory's mtime -- so the parent answers for it. Without
# that, an uncommitted deletion stats nothing, reads as just-written, and the
# one change git cannot recover would be the one that does not block.
# The clock, read once. Non-zero when it cannot be: an unread clock is not a
# time to work from.
clock_now() {
  local now status=0
  now="$(date +%s)" || status=$?
  # The status is checked before the output: a non-zero `date` that still prints
  # digits is a failed clock read, not a reading to accept.
  if [ "$status" -ne 0 ]; then
    echo "check-leftovers: cannot read the system clock (date +%s exited ${status}) -- no age can be computed, so no worktree can be classified" >&2
    return 1
  fi
  case "$now" in
    ''|*[!0-9]*)
      echo "check-leftovers: the system clock reported '${now}', which is not a whole number of seconds -- no age can be computed, so no worktree can be classified" >&2
      return 1
      ;;
  esac
  echo "$now"
}

# Seconds since a path was last written, against a clock the caller already
# read. Non-zero when the mtime cannot be read at all: an age this script had to
# guess would decide a verdict, and a gate that cannot tell must refuse rather
# than answer the reassuring way.
#
# A path that no longer exists is a deletion, and deleting it is exactly what
# updated its parent directory's mtime -- so the parent answers for it. Without
# that, an uncommitted deletion stats nothing and the one change git cannot
# recover would be the one that does not block.
age_seconds() { # <now> <path>
  local now="$1" path="$2" target mtime
  target="$path"
  if [ ! -e "$target" ] && [ ! -L "$target" ]; then
    target="$(dirname -- "$path")"
  fi
  # GNU `stat` is probed first: BSD's `-f` is GNU's --file-system, which answers
  # an unrelated question and SUCCEEDS while doing it, so probing the other way
  # round yields a non-time on Linux and every age reads as zero.
  mtime="$(stat -c %Y "$target" 2>/dev/null)" || mtime=""
  [ -n "$mtime" ] || mtime="$(stat -f %m "$target" 2>/dev/null)" || mtime=""
  case "$mtime" in
    ''|*[!0-9]*)
      echo "check-leftovers: cannot read the modification time of ${target} -- check the path is readable, then re-run" >&2
      return 1
      ;;
  esac
  echo $(( now - mtime ))
}

# Every path git reports as changed, with its staged/unstaged/untracked counts,
# read into the WT_* globals. Non-zero, naming git's own message, when git could
# not read the worktree: an unreadable worktree and a clean one are opposite
# answers and must not collapse into the same zero.
#
# Parsed from the NUL-separated records directly, never through a line-oriented
# intermediate, because a path may legally contain a newline. A rename or copy
# record is followed by a second NUL field holding the ORIGINAL path with no
# status prefix; it is consumed here so it cannot be mistaken for a record of
# its own. `--untracked-files=all` rather than `normal`: normal collapses an
# untracked directory to a single entry, and a directory's mtime does not move
# when a file already inside it is edited, so a fresh edit under an old
# directory would read as abandoned.
read_status() { # <worktree>
  local wt="$1" file="${SCRATCH}/status" record x y path origin
  WT_PATHS=(); WT_STAGED=0; WT_UNSTAGED=0; WT_UNTRACKED=0

  if ! git -C "$wt" status --porcelain -z --untracked-files=all >"$file" 2>"$ERR_SINK"; then
    echo "check-leftovers: cannot read git status in ${wt}: $(cat "$ERR_SINK")" >&2
    return 1
  fi

  while IFS= read -r -d '' record; do
    [ ${#record} -ge 3 ] || {
      echo "check-leftovers: ${wt} reported a status record shorter than its own prefix -- run 'git -C ${wt} status --porcelain -z' to see it" >&2
      return 1
    }
    x="${record:0:1}"; y="${record:1:1}"; path="${record:3}"

    if [ "$x$y" = '??' ]; then
      WT_UNTRACKED=$(( WT_UNTRACKED + 1 ))
    else
      # Every code other than a space counts, not a hand-picked set: `T` for a
      # type change and `U` for an unmerged path are dirt too, and a worktree
      # holding only those would otherwise count zero and let the release run.
      [ "$x" = ' ' ] || WT_STAGED=$(( WT_STAGED + 1 ))
      [ "$y" = ' ' ] || WT_UNSTAGED=$(( WT_UNSTAGED + 1 ))
    fi
    WT_PATHS+=("$path")

    case "$x$y" in
      R?|C?|?R|?C)
        # shellcheck disable=SC2034  # Read to consume the field, not to use
        # it: the original path of a rename is gone from disk and has no age.
        if ! IFS= read -r -d '' origin; then
          echo "check-leftovers: ${wt}'s status ended part-way through a rename record -- run 'git -C ${wt} status --porcelain -z' to see it" >&2
          return 1
        fi
        ;;
    esac
  done < "$file"
  return 0
}

# The newest write among the paths read_status collected, in whole hours. The
# paths are relative to the worktree git read them from, so that root is an
# argument rather than something this function could guess.
dirt_age_hours() { # <worktree>
  local wt="$1" newest=999999999 rel age now
  now="$(clock_now)" || return 1
  for rel in ${WT_PATHS+"${WT_PATHS[@]}"}; do
    age="$(age_seconds "$now" "${wt}/${rel}")" || return 1
    [ "$age" -lt "$newest" ] && newest="$age"
  done
  [ "$newest" -eq 999999999 ] && newest=0
  # A path dated in the future is a skewed clock or a skewed mtime, never an old
  # one. It reads as just-written, and says so rather than producing the
  # negative age that would sort ahead of every real one.
  if [ "$newest" -lt 0 ]; then
    echo "check-leftovers: a changed path in ${wt} is dated in the future -- treating it as just written; check the system clock and the file's timestamp" >&2
    newest=0
  fi
  echo $(( newest / 3600 ))
}

# A worktree's branch name, or DETACHED when HEAD points at no branch -- which
# `--abbrev-ref` reports by printing HEAD and exiting 0. A non-zero exit is a
# tool error and is never dressed up as a branch name.
branch_of() { # <worktree>
  local name err status=0
  err="$(git -C "$1" rev-parse --abbrev-ref HEAD 2>&1)" || status=$?
  if [ "$status" -ne 0 ]; then
    echo "check-leftovers: cannot read the checked-out branch in ${1} (exit ${status}): ${err}" >&2
    return 1
  fi
  name="$err"
  [ "$name" = "HEAD" ] && name="DETACHED"
  echo "$name"
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

  case "$LEFTOVERS_MIN_AGE_HOURS" in
    ''|*[!0-9]*)
      die "LEFTOVERS_MIN_AGE_HOURS is '${LEFTOVERS_MIN_AGE_HOURS}' -- set it to a whole number of hours (0 or more), or unset it to use the default" ;;
  esac

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

  # A ref that does not resolve is not the same as a ref that is not there. If
  # origin/main exists in the ref store but will not resolve to a commit, the
  # repository is damaged, and judging a worktree against local `main` instead
  # would answer the wrong question rather than refuse to answer.
  local base="origin/main" present
  if ! git -C "$repo" rev-parse --verify --quiet "${base}^{commit}" >/dev/null; then
    # `for-each-ref` answers existence alone: it exits 0 either way and prints
    # the name only when the ref is there. `show-ref --verify` cannot be asked
    # this -- it exits non-zero for a damaged ref and for an absent one alike,
    # which is the distinction being drawn.
    present="$(git -C "$repo" for-each-ref --format='%(refname)' "refs/remotes/${base}" 2>"$ERR_SINK")" \
      || die "cannot read ${base} in ${repo}: $(cat "$ERR_SINK")"
    if [ -n "$present" ]; then
      die "${base} exists but does not resolve to a commit -- the ref is damaged; run 'git -C ${repo} fsck', then re-run"
    fi
    base="main"
    if ! git -C "$repo" rev-parse --verify --quiet "${base}^{commit}" >/dev/null; then
      die "neither origin/main nor main resolves -- fetch the remote, then re-run"
    fi
  fi

  local blocking=() others_json=() ok=true

  local self_branch staged unstaged untracked self_tip_in_main self_age self_verdict
  self_branch="$(branch_of "$self_path")" \
    || die "cannot read the checked-out branch in ${self_path} -- see the diagnostic above"
  read_status "$self_path" \
    || die "cannot read the working tree at ${self_path} -- see the diagnostic above"
  staged="$WT_STAGED"; unstaged="$WT_UNSTAGED"; untracked="$WT_UNTRACKED"
  self_tip_in_main="$(tip_is_in "$self_path" HEAD "$base")" \
    || die "cannot classify ${self_path} against ${base} -- see the diagnostic above"
  self_age="$(dirt_age_hours "$self_path")" \
    || die "cannot age the changes in ${self_path} -- see the diagnostic above"
  self_verdict="clean"
  if [ $(( staged + unstaged + untracked )) -gt 0 ]; then
    self_verdict="in_progress"
    if [ "$self_tip_in_main" = true ] && [ "$self_age" -ge "$LEFTOVERS_MIN_AGE_HOURS" ]; then
      self_verdict="abandoned"
    fi
    ok=false
    blocking+=("$(printf 'this worktree (%s) has %d staged, %d unstaged and %d untracked path(s); a release publishes only what is committed' \
      "$self_branch" "$staged" "$unstaged" "$untracked")")
  fi

  # `--porcelain -z`, and read as NUL records: a worktree path may contain a
  # newline, and the newline-delimited form would split it into two worktrees
  # that are each neither.
  local inventory="${SCRATCH}/worktrees"
  git -C "$repo" worktree list --porcelain -z >"$inventory" 2>"$ERR_SINK" \
    || die "cannot read the worktree list for ${repo}: $(cat "$ERR_SINK")"

  local field wt branch tip_in_main age verdict
  while IFS= read -r -d '' field; do
    case "$field" in
      'worktree '*) wt="${field#worktree }" ;;
      *) continue ;;
    esac
    [ -n "$wt" ] || continue
    [ "$wt" = "$self_path" ] && continue
    if [ ! -d "$wt" ]; then
      # A registered worktree whose directory is gone took its files with it and
      # holds nothing to lose. One that exists and is not a searchable directory
      # may hold the only copy of the work, and skipping it silently is the
      # fail-open this gate exists to close.
      if [ -e "$wt" ] || [ -L "$wt" ]; then
        die "${wt} is registered as a worktree but is not a readable directory -- fix its permissions, or run 'git -C ${repo} worktree prune', then re-run"
      fi
      continue
    fi
    read_status "$wt" \
      || die "cannot read the working tree at ${wt} -- see the diagnostic above"
    [ "${#WT_PATHS[@]}" -gt 0 ] || continue

    branch="$(branch_of "$wt")" \
      || die "cannot read the checked-out branch in ${wt} -- see the diagnostic above"
    tip_in_main="$(tip_is_in "$wt" HEAD "$base")" \
      || die "cannot classify ${wt} against ${base} -- see the diagnostic above"
    age="$(dirt_age_hours "$wt")" \
      || die "cannot age the changes in ${wt} -- see the diagnostic above"

    verdict="in_progress"
    if [ "$tip_in_main" = true ] && [ "$age" -ge "$LEFTOVERS_MIN_AGE_HOURS" ]; then
      verdict="abandoned"
      ok=false
      blocking+=("$(printf '%s holds uncommitted work on %s, a branch carrying no commits of its own, last written %dh ago; nothing in git preserves it' \
        "$wt" "$branch" "$age")")
    fi
    others_json+=("$(printf '{"path":%s,"branch":%s,"tip_in_main":%s,"age_hours":%d,"verdict":%s}' \
      "$(json_str "$wt")" "$(json_str "$branch")" "$tip_in_main" "$age" "$(json_str "$verdict")")")
  done < "$inventory"

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
