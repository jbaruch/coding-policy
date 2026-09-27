#!/usr/bin/env bash
# Clean this session's repository at session start; report what only the
# operator can decide.
#
# Worktrees and branches pile up faster than anyone removes them by hand. This
# hook runs the two owner scripts for the session's repository, live, so
# everything recoverable from origin goes without a word: idle clean worktrees
# origin holds, local branches origin holds or merged, and branches on origin
# merged with no open pull request. The decisions are the owner scripts', never
# this hook's (rules/script-as-black-box.md):
#   - skills/herdr-foreman/prune-worktrees.sh — worktrees and local branches
#   - skills/herdr-foreman/prune-remote-branches.sh — branches on origin
# What they keep because it exists nowhere else is listed for the operator:
# idle dirty or unpushed worktrees, idle local branches with unpushed commits,
# and stale branches on origin merged nowhere with no pull request. Nothing
# else is listed: no removals, no counts, no other repository.
#
# Never acts in a Herdr worker session (HERDR_ENV set in a linked worktree):
# workers never delete (rules/agent-team-operation.md Writers and Checkouts).
# In portable mode (SESSION_START_MODE=portable, set by hooks/session-start.sh
# under `tessl hook run`, which strips HERDR_ENV) the scripts run --dry-run:
# the list is the same, and nothing is deleted.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read.
#   stdout: at most one JSON object {"additionalContext": "<status>"}. The
#           status holds up to two "Session-start status — " paragraphs: the
#           items awaiting the operator's decision, each with its command, and
#           one "could not check" line naming every owner script that failed,
#           ran out of time, reported an undecided item, or could not reach
#           gh, with the command to rerun it. A missing git or python3 is
#           that line too, a fixed JSON string printed without either tool.
#           Silent when neither applies, outside a repository, in a
#           repository without an origin remote, in a bare repository, and in
#           a Herdr worker session.
#   stderr: the owner scripts' diagnostics, relayed, plus this hook's warnings.
#   exit  : always 0 (a failure is the "could not check" line, never silence
#           and never a failed session start).
#   env   : LEFTOVER_BUDGET_SEC overrides BUDGET_SEC; WORKTREE_ROOT and the
#           PRUNE_* variables pass through to the owner scripts.
set -euo pipefail

#: Wall-clock seconds for both owner scripts together. Session start waits on
#: this hook, and the network is the part that can hang.
BUDGET_SEC="${LEFTOVER_BUDGET_SEC:-40}"

warn() { printf 'check-leftover-worktrees: %s\n' "$1" >&2; }

#: The scratch directory, global so the RETURN trap can name a function
#: instead of interpolating a path into shell source.
SCRATCH=""

discard() {
  if [[ -n "$SCRATCH" ]] && ! rm -rf "$SCRATCH"; then
    warn "could not remove the temporary directory ${SCRATCH} — delete it by hand"
  fi
  return 0
}

# Print a could-not-check status that needs no tool to encode. <why> is one
# of this script's own fixed ASCII sentences, never input: no character in
# it needs JSON escaping.
static_cannot_check() { # <fixed-why>
  printf '{"additionalContext": "Session-start status \\u2014 could not check this repository for leftover worktrees and branches: %s"}\n' "$1"
}

# Print the could-not-check status alone, for a failure before any result.
# When python3 cannot encode <why>, the fixed status still reaches the session.
cannot_check() { # <why>
  if ! python3 -c 'import json, sys; print(json.dumps({"additionalContext": sys.argv[1]}))' \
      "Session-start status — could not check this repository for leftover worktrees and branches: $1"; then
    warn "python3 could not encode the status (${1}) — reporting the fixed status instead"
    static_cannot_check "python3 failed; run this hook by hand to see why."
  fi
}

# Relay one owner script's stderr, each line prefixed with this hook's name.
relay() { # <file>
  local line
  [[ -s "$1" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -n "$line" ]]; then warn "$line"; fi
  done < "$1"
  return 0
}

main() {
  if ! command -v git >/dev/null; then
    warn "git not found on PATH — install it so session start can clean this repository"
    static_cannot_check "git is not on PATH; install it, then start a new session."
    return 0
  fi
  if ! command -v python3 >/dev/null; then
    warn "python3 not found on PATH — install it so session start can clean this repository"
    static_cannot_check "python3 is not on PATH; install it, then start a new session."
    return 0
  fi
  # `rev-parse` exits 128 both outside a repository and for one it cannot
  # read; only the walked-up-and-found-nothing message is silent.
  local err rc=0
  err="$(git rev-parse --git-dir 2>&1 >/dev/null)" || rc=$?
  if (( rc != 0 )); then
    case "$err" in
      *"or any of the parent directories"*) ;;
      *) cannot_check "git cannot read it (${err:-git exited ${rc} silently}); run \`git status\` here to see why." ;;
    esac
    return 0
  fi

  local git_dir common_dir
  rc=0
  git_dir="$(git rev-parse --path-format=absolute --git-dir 2>&1)" || rc=$?
  if (( rc == 0 )); then common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>&1)" || rc=$?; fi
  if (( rc != 0 )); then
    cannot_check "\`git rev-parse --git-common-dir\` exited ${rc}; run it here to see why."
    return 0
  fi
  # A worker session: acting here would break the rule the worker runs under.
  if [[ -n "${HERDR_ENV:-}" && "$git_dir" != "$common_dir" ]]; then
    return 0
  fi

  # Exit 2 is git's "no such remote": nothing is recoverable from origin, so
  # nothing is judged.
  rc=0
  err="$(git remote get-url origin 2>&1 >/dev/null)" || rc=$?
  case "$rc" in
    0) ;;
    2) return 0 ;;
    *) cannot_check "\`git remote get-url origin\` exited ${rc} (${err}); run it here to see why."; return 0 ;;
  esac

  # The shared checkout is the first entry of the worktree list, whichever
  # worktree this session sits in; a bare repository has no checkout to clean.
  local shared
  rc=0
  err="$(mktemp)" || { cannot_check "mktemp failed; make ${TMPDIR:-/tmp} writable."; return 0; }
  shared="$(git worktree list --porcelain -z 2>"$err" | python3 -c '
import sys
first = []
for field in sys.stdin.buffer.read().split(b"\0"):
    if not field:
        break
    first.append(field)
if not first or not first[0].startswith(b"worktree "):
    sys.exit(3)
if b"bare" in first:
    sys.exit(4)
sys.stdout.buffer.write(first[0][len(b"worktree "):] + b"x")
')" || rc=$?
  # The sentinel keeps a trailing newline in the path through the command
  # substitution; it comes off only here.
  shared="${shared%x}"
  local list_err
  list_err="$(cat "$err")"
  if ! rm -f "$err"; then warn "could not remove ${err} — delete it by hand"; fi
  case "$rc" in
    0) ;;
    4) return 0 ;;
    *) cannot_check "\`git worktree list --porcelain -z\` gave no worktree (exit ${rc}; ${list_err}); run it here to see why."; return 0 ;;
  esac

  local here
  # `pwd` through command substitution loses a trailing newline in the plugin
  # directory's own name; the sentinel survives the strip (#466).
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd && printf x)"
  here="${here%x}"
  here="${here%$'\n'}"
  local runner="${here}/../skills/herdr-foreman/bounded-run.sh" prune="${here}/../skills/herdr-foreman/prune-worktrees.sh"
  local remote="${here}/../skills/herdr-foreman/prune-remote-branches.sh" f
  for f in "$runner" "$prune" "$remote"; do
    if [[ ! -f "$f" || ! -r "$f" ]]; then
      cannot_check "${f} is not readable; reinstall the plugin."
      return 0
    fi
  done

  SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/leftover-hook.XXXXXX")" || {
    cannot_check "no temporary directory could be created under ${TMPDIR:-/tmp}; make it writable."
    return 0
  }
  trap discard RETURN

  local -a mode_args=()
  if [[ "${SESSION_START_MODE:-native}" == portable ]]; then mode_args=(--dry-run); fi
  # Nothing may stop to ask for a credential: an unattended prompt is a hang.
  export GIT_TERMINAL_PROMPT=0 GH_PROMPT_DISABLED=1
  export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}"

  local start=$SECONDS left prune_rc=0 remote_rc=0
  bash "$runner" "$BUDGET_SEC" bash "$prune" "$shared" ${mode_args[@]+"${mode_args[@]}"} \
    >"${SCRATCH}/prune.json" 2>"${SCRATCH}/prune.err" || prune_rc=$?
  relay "${SCRATCH}/prune.err"
  left=$(( BUDGET_SEC - (SECONDS - start) ))
  if (( left > 0 )); then
    bash "$runner" "$left" bash "$remote" "$shared" ${mode_args[@]+"${mode_args[@]}"} \
      >"${SCRATCH}/remote.json" 2>"${SCRATCH}/remote.err" || remote_rc=$?
    relay "${SCRATCH}/remote.err"
  else
    remote_rc=124
  fi

  rc=0
  python3 - "$shared" "$prune" "$prune_rc" "${SCRATCH}/prune.json" \
      "$remote" "$remote_rc" "${SCRATCH}/remote.json" "${mode_args[*]-}" <<'PY' || rc=$?
import json
import shlex
import sys

shared, prune, prune_rc, prune_out, remote, remote_rc, remote_out, flags = sys.argv[1:9]
items, failures = [], []


def load(path, rc, script):
    """The script's JSON when it answered (exit 0, or 2 with its JSON)."""
    rerun = "`bash {} {}{}`".format(shlex.quote(script), shlex.quote(shared), " " + flags if flags else "")
    name = script.rsplit("/", 1)[-1]
    if rc == "124":
        failures.append("{} ran past its time budget (run {})".format(name, rerun))
        return None
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        doc = None
    if rc not in ("0", "2") or not isinstance(doc, dict):
        failures.append("{} exited {} (run {})".format(name, rc, rerun))
        return None
    if doc.get("failed"):
        failures.append("{} could not decide {} item(s) (run {})".format(name, len(doc["failed"]), rerun))
    if doc.get("could_not_check"):
        failures.append("{}: {}".format(name, doc["could_not_check"]))
    return doc


local = load(prune_out, prune_rc, prune)
if local is not None:
    for kept in local.get("worktrees_kept", []):
        where = "{} ({})".format(kept["path"], kept.get("branch") or "detached")
        if kept.get("reason") == "dirty":
            items.append("worktree {}: {} uncommitted file(s), idle {}h — `{}`".format(
                where, kept["dirty_files"], kept["age_hours"], kept["command"]))
        elif kept.get("reason") == "unpushed":
            items.append("worktree {}: {} commit(s) origin does not hold, idle {}h — `{}`".format(
                where, kept["unpushed_commits"], kept["age_hours"], kept["command"]))
    for kept in local.get("branches_kept", []):
        if kept.get("reason") == "unpushed":
            items.append("local branch {}: {} unpushed commit(s), idle {}h — `{}`".format(
                kept["branch"], kept["unpushed_commits"], kept["age_hours"], kept["command"]))

origin = load(remote_out, remote_rc, remote)
if origin is not None:
    for q in origin.get("questionable", []):
        items.append("origin branch {}: {} commit(s) not in {}, no open pull request, last commit {}h ago by {} — `{}` or `{}`".format(
            q["branch"], q["ahead"], origin.get("default_branch"), q["age_hours"], q["author"], q["open_pr"], q["delete"]))

paragraphs = []
if items:
    lines = ["Session-start status — {} item(s) in this repository hold work only the operator can decide on; "
             "nothing was touched:".format(len(items))]
    lines += ["  - " + item for item in items]
    lines.append("Raise each with the user, one at a time: push, commit, open a pull request, delete or keep.")
    paragraphs.append("\n".join(lines))
if failures:
    paragraphs.append("Session-start status — could not check this repository for leftover worktrees and branches: "
                      + "; ".join(failures) + ".")
if paragraphs:
    print(json.dumps({"additionalContext": "\n\n".join(paragraphs)}))
PY
  if (( rc != 0 )); then
    cannot_check "the owner scripts' results could not be read (exit ${rc}); run \`bash ${prune} ${shared} --dry-run\` to see them."
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no leftover report this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
    static_cannot_check "the hook failed internally; run hooks/check-leftover-worktrees.sh by hand to see why."
  fi
  exit 0
fi
