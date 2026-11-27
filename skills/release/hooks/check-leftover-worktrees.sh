#!/usr/bin/env bash
# Clean this session's repository at session start; report what only the
# operator can decide.
#
# Worktrees and branches pile up faster than anyone removes them by hand. This
# hook runs the owner script for the session's repository, live, so
# everything recoverable from origin goes without a word: idle clean worktrees
# origin holds and local branches origin holds or merged. The decisions are
# the owner script's, never this hook's (rules/script-as-black-box.md):
#   - lib/prune-worktrees.sh — worktrees and local branches
# What it keeps because it exists nowhere else is listed for the operator:
# idle dirty or unpushed worktrees and idle local branches with unpushed commits.
# Nothing else is listed: no removals, no counts, no other repository.
# GitHub's delete-branch-on-merge setting handles merged PR branches on origin.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read.
#   stdout: at most one JSON object {"additionalContext": "<status>"}. The
#           status holds up to two "Session-start status — " paragraphs: the
#           items awaiting the operator's decision, each with its command, and
#           one "could not check" line naming the owner script if it failed,
#           ran out of time or reported an undecided item, with the command
#           to rerun it. A missing git or python3 is
#           that line too, a fixed JSON string printed without either tool.
#           Silent when neither applies, outside a repository, in a
#           repository without an origin remote, and in a bare repository.
#   stderr: the owner script's diagnostics, relayed, plus this hook's warnings.
#   exit  : always 0 (a failure is the "could not check" line, never silence
#           and never a failed session start).
#   env   : LEFTOVER_BUDGET_SEC overrides BUDGET_SEC; WORKTREE_ROOT and the
#           PRUNE_* variables pass through to the owner script.
set -euo pipefail

#: Wall-clock seconds for the owner script. Session start waits on
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
  local shared list list_err
  rc=0
  list="$(mktemp -d "${TMPDIR:-/tmp}/check-leftover-worktrees.XXXXXX")" || { cannot_check "mktemp failed; make ${TMPDIR:-/tmp} writable."; return 0; }
  # Captured to a file and git's own exit checked before anything is parsed,
  # as hooks/stop-handoff-hygiene.sh reads it: a git failure is named as one,
  # never folded into the parser's verdict.
  git worktree list --porcelain -z >"${list}/out" 2>"${list}/err" || rc=$?
  list_err="$(cat "${list}/err")"
  if (( rc != 0 )); then
    if ! rm -rf "$list"; then warn "could not remove ${list} — delete it by hand"; fi
    cannot_check "\`git worktree list --porcelain -z\` exited ${rc} (${list_err}); run it here to see why."
    return 0
  fi
  shared="$(python3 -c '
import sys
first = []
with open(sys.argv[1], "rb") as handle:
    fields = handle.read().split(b"\0")
for field in fields:
    if not field:
        break
    first.append(field)
if not first or not first[0].startswith(b"worktree "):
    sys.exit(3)
if b"bare" in first:
    sys.exit(4)
sys.stdout.buffer.write(first[0][len(b"worktree "):] + b"x")
' "${list}/out")" || rc=$?
  # The sentinel keeps a trailing newline in the path through the command
  # substitution; it comes off only here.
  shared="${shared%x}"
  if ! rm -rf "$list"; then warn "could not remove ${list} — delete it by hand"; fi
  case "$rc" in
    0) ;;
    4) return 0 ;;
    *) cannot_check "\`git worktree list --porcelain -z\` gave no worktree (parse exit ${rc}; ${list_err}); run it here to see why."; return 0 ;;
  esac

  local src dir here
  # Command substitution strips every trailing newline, so the hooks directory
  # never passes through one bare: parameter expansion derives it (#487), and a
  # sentinel carries `pwd` across the strip (#466).
  src="${BASH_SOURCE[0]}"
  case "$src" in
    */*) dir="${src%/*}" ;;
    *) dir=. ;;
  esac
  if ! here="$(CDPATH='' cd -- "${dir:-/}" && pwd && printf x)"; then
    cannot_check "the hooks directory ${dir:-/} cannot be entered; reinstall the plugin."
    return 0
  fi
  here="${here%x}"
  here="${here%$'\n'}"
  local runner="${here}/../lib/bounded-run.sh" prune="${here}/../lib/prune-worktrees.sh"
  local f
  for f in "$runner" "$prune"; do
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

  # Nothing may stop to ask for a credential: an unattended prompt is a hang.
  export GIT_TERMINAL_PROMPT=0
  # Appended, never a default: a user-set command keeps BatchMode too.
  export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh} -o BatchMode=yes"

  local prune_rc=0
  bash "$runner" "$BUDGET_SEC" bash "$prune" "$shared" \
    >"${SCRATCH}/prune.json" 2>"${SCRATCH}/prune.err" || prune_rc=$?
  relay "${SCRATCH}/prune.err"

  rc=0
  python3 - "$shared" "$prune" "$prune_rc" "${SCRATCH}/prune.json" <<'PY' || rc=$?
import json
import os
import shlex
import sys

shared, prune, prune_rc, prune_out = sys.argv[1:5]
items, failures = [], []


# The result shape belongs to the owner script (lib/prune_result.py).
sys.path.insert(0, os.path.dirname(prune))
from prune_result import prune_schema_error


def load(path, rc, script):
    """The script's JSON when it answered (exit 0, or 2 with its JSON) in its
    documented shape; anything else is a failure, never a clean result."""
    rerun = "`bash {} {}`".format(shlex.quote(script), shlex.quote(shared))
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
    why = prune_schema_error(doc)
    if why:
        failures.append("{} exited {} with a result holding {} (run {})".format(name, rc, why, rerun))
        return None
    if doc.get("failed"):
        failures.append("{} could not decide {} item(s) (run {})".format(name, len(doc["failed"]), rerun))
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

paragraphs = []
if items:
    lines = ["Session-start status — {} item(s) in this repository hold work only the operator can decide on; "
             "nothing was touched:".format(len(items))]
    lines += ["  - " + item for item in items]
    lines.append("Ask the user what to do with each item. Offer applicable choices to push, commit, open a pull request, delete or keep.")
    paragraphs.append("\n".join(lines))
if failures:
    paragraphs.append("Session-start status — could not check this repository for leftover worktrees and branches: "
                      + "; ".join(failures) + ".")
if paragraphs:
    print(json.dumps({"additionalContext": "\n\n".join(paragraphs)}))
PY
  if (( rc != 0 )); then
    cannot_check "the owner script's result could not be read (exit ${rc}); run \`bash ${prune} ${shared} --dry-run\` to see it."
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no leftover report this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
    static_cannot_check "the hook failed internally; run skills/release/hooks/check-leftover-worktrees.sh by hand to see why."
  fi
  exit 0
fi
