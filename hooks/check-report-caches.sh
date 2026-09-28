#!/usr/bin/env bash
# Remove regenerable build and package caches from idle Herdr reports
# directories at session start; report what was reclaimed and what failed.
#
# Workers pointed build and package caches at their reports directories, and
# nothing removed them. The decision of what is a cache, which reports
# directories exist, and which are idle belongs to the owner script, never
# this hook (rules/script-as-black-box.md):
#   - skills/herdr-foreman/prune-report-caches.py (top-of-file docstring)
# This hook runs it live, so the safe mechanical fix happens without a word
# beyond the bytes it reclaimed.
#
# Mode, mirroring hooks/check-leftover-worktrees.sh:
#   - Herdr worker session (HERDR_ENV set, even empty) in a linked worktree,
#     or in a checkout git cannot place: nothing runs, nothing is printed.
#     Workers never delete (rules/agent-team-operation.md Writers and
#     Checkouts).
#   - Portable mode (SESSION_START_MODE=portable, set by
#     hooks/session-start.sh under `tessl hook run`, which strips HERDR_ENV):
#     a linked worktree may be a worker's, so nothing runs there; anywhere
#     else the owner script runs --dry-run and the status names what a native
#     session would remove.
#   - Every other session runs it live.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read.
#   stdout: at most one JSON object {"additionalContext": "<status>"}. The
#           status holds "Session-start status — " paragraphs: the caches
#           removed (or, in portable mode, removable) with their total size,
#           the removals that failed, and a "could not check" line when the
#           ledger could not be read, the owner script failed or ran out of
#           time, or python3 is missing, each with the command to rerun.
#           Silent when there is nothing to report and in the skipped modes.
#   stderr: the owner script's diagnostics, relayed, plus this hook's warnings.
#   exit  : always 0 (a failure is a status line, never a failed session start).
#   env   : REPORT_CACHES_BUDGET_SEC overrides BUDGET_SEC; REPORT_CACHES_NOW
#           (tests only) passes --now to the owner script.
set -euo pipefail

#: Wall-clock seconds the owner script may take. Session start waits on this
#: hook; the owner script stops starting new removals SOFT_MARGIN_SEC earlier
#: and reports the rest for the next session.
BUDGET_SEC="${REPORT_CACHES_BUDGET_SEC:-30}"
SOFT_MARGIN_SEC=10

warn() { printf 'check-report-caches: %s\n' "$1" >&2; }

SCRATCH=""

discard() {
  if [[ -n "$SCRATCH" ]] && ! rm -rf "$SCRATCH"; then
    warn "could not remove the temporary directory ${SCRATCH} — delete it by hand"
  fi
  return 0
}

# Print a could-not-check status that needs no tool to encode. <why> is one
# of this script's own fixed ASCII sentences, never input.
static_cannot_check() { # <fixed-why>
  printf '{"additionalContext": "Session-start status \\u2014 could not prune build caches from Herdr reports directories: %s"}\n' "$1"
}

relay() { # <file>
  local line
  [[ -s "$1" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -n "$line" ]]; then warn "$line"; fi
  done < "$1"
  return 0
}

# Print yes when this session sits in a linked worktree, no when it provably
# does not (a main checkout, or no repository at all), unknown otherwise.
linked_worktree() {
  local err rc=0 git_dir common_dir
  if ! command -v git >/dev/null; then
    printf 'unknown'
    return 0
  fi
  err="$(git rev-parse --git-dir 2>&1 >/dev/null)" || rc=$?
  if (( rc != 0 )); then
    case "$err" in
      *"or any of the parent directories"*) printf 'no' ;;
      *) printf 'unknown' ;;
    esac
    return 0
  fi
  if ! git_dir="$(git rev-parse --path-format=absolute --git-dir)" \
      || ! common_dir="$(git rev-parse --path-format=absolute --git-common-dir)"; then
    printf 'unknown'
    return 0
  fi
  if [[ "$git_dir" == "$common_dir" ]]; then printf 'no'; else printf 'yes'; fi
}

main() {
  local mode="${SESSION_START_MODE:-native}" linked
  linked="$(linked_worktree)"
  if [[ -n "${HERDR_ENV+x}" && "$linked" != no ]]; then
    return 0
  fi
  if [[ "$mode" == portable && "$linked" != no ]]; then
    return 0
  fi
  if ! command -v python3 >/dev/null; then
    warn "python3 not found on PATH — install it so session start can prune report caches"
    static_cannot_check "python3 is not on PATH; install it, then start a new session."
    return 0
  fi

  local src dir here
  # Command substitution strips every trailing newline, so the hooks directory
  # never passes through one bare: parameter expansion derives it (#487), and a
  # sentinel carries `pwd` across the strip (#466).
  src="${BASH_SOURCE[0]}"
  case "$src" in
    */*) dir="${src%/*}" ;;
    *) dir=. ;;
  esac
  if ! here="$(cd -- "${dir:-/}" && pwd && printf x)"; then
    static_cannot_check "the hooks directory cannot be entered; reinstall the plugin."
    return 0
  fi
  here="${here%x}"
  here="${here%$'\n'}"
  local runner="${here}/../skills/herdr-foreman/bounded-run.sh"
  local prune="${here}/../skills/herdr-foreman/prune-report-caches.py" f
  for f in "$runner" "$prune"; do
    if [[ ! -f "$f" || ! -r "$f" ]]; then
      static_cannot_check "a co-shipped owner script is not readable; reinstall the plugin."
      return 0
    fi
  done

  SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/report-caches-hook.XXXXXX")" || {
    static_cannot_check "no temporary directory could be created; make TMPDIR writable."
    return 0
  }
  trap discard RETURN

  case "$BUDGET_SEC" in
    '' | *[!0-9]* | 0*)
      warn "REPORT_CACHES_BUDGET_SEC must be a positive whole number of seconds — unset it or set one, e.g. 30"
      static_cannot_check "REPORT_CACHES_BUDGET_SEC is not a positive whole number of seconds; unset it or set one, e.g. 30."
      return 0 ;;
  esac
  local soft=$(( BUDGET_SEC - SOFT_MARGIN_SEC ))
  (( soft > 0 )) || soft=1
  local -a args=(--budget-sec "$soft")
  if [[ "$mode" == portable ]]; then args+=(--dry-run); fi
  if [[ -n "${REPORT_CACHES_NOW:-}" ]]; then args+=(--now "$REPORT_CACHES_NOW"); fi

  local rc=0
  bash "$runner" "$BUDGET_SEC" python3 "$prune" "${args[@]}" \
    >"${SCRATCH}/out.json" 2>"${SCRATCH}/err" || rc=$?
  relay "${SCRATCH}/err"

  local render_rc=0
  python3 - "$prune" "$rc" "${SCRATCH}/out.json" "$mode" <<'PY' || render_rc=$?
import json
import shlex
import sys

prune, rc, out, mode = sys.argv[1:5]
rerun = "`python3 {}`".format(shlex.quote(prune))
head = "Session-start status — "


def size(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return "{:.1f} {}".format(n, unit) if unit != "B" else "{} B".format(n)
        n /= 1024.0
    return "{:.1f} TiB".format(n)


def emit(paragraphs):
    if paragraphs:
        print(json.dumps({"additionalContext": "\n\n".join(paragraphs)}))


if rc == "124":
    emit([head + "could not prune build caches from Herdr reports directories: the owner script ran past "
          "its time budget; run {} to finish.".format(rerun)])
    sys.exit(0)
try:
    with open(out, encoding="utf-8") as handle:
        doc = json.load(handle)
except (OSError, ValueError):
    doc = None
shape_ok = (isinstance(doc, dict) and isinstance(doc.get("caches"), list) and isinstance(doc.get("failed"), list)
            and isinstance(doc.get("bytes"), int) and isinstance(doc.get("incomplete"), bool)
            and all(isinstance(c, dict) and isinstance(c.get("path"), str) for c in doc["caches"])
            and all(isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("error"), str)
                    for f in doc["failed"]))
if rc not in ("0", "2") or not shape_ok:
    emit([head + "could not prune build caches from Herdr reports directories: the owner script exited {} "
          "without a readable result; run {} to see why.".format(rc, rerun)])
    sys.exit(0)

paragraphs = []
if doc.get("could_not_check"):
    paragraphs.append(head + "could not prune build caches from Herdr reports directories: {}; run {} to see "
                      "it.".format(doc["could_not_check"], rerun))
count = len(doc["caches"])
if count:
    if mode == "portable":
        paragraphs.append(head + "{} regenerable build cache director{} ({}) sit in idle Herdr reports directories; "
                          "nothing was removed under tessl. Run {} to remove them.".format(
                              count, "y" if count == 1 else "ies", size(doc["bytes"]), rerun))
    else:
        paragraphs.append(head + "removed {} regenerable build cache director{} ({}) from idle Herdr reports "
                          "directories; evidence files were kept.".format(
                              count, "y" if count == 1 else "ies", size(doc["bytes"])))
if doc["incomplete"]:
    paragraphs.append(head + "the report-cache prune stopped at its time budget with directories left to check; "
                      "the next session continues, or run {} now.".format(rerun))
if doc["failed"]:
    listed = "; ".join("{} ({})".format(f["path"], f["error"]) for f in doc["failed"])
    paragraphs.append(head + "could not remove {} build cache director{}: {}. Fix the named cause, then run "
                      "{}.".format(len(doc["failed"]), "y" if len(doc["failed"]) == 1 else "ies", listed, rerun))
emit(paragraphs)
PY
  if (( render_rc != 0 )); then
    static_cannot_check "the owner script's result could not be rendered; run skills/herdr-foreman/prune-report-caches.py by hand to see it."
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no report-cache status this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
    static_cannot_check "the hook failed internally; run hooks/check-report-caches.sh by hand to see why."
  fi
  exit 0
fi
