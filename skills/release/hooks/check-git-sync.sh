#!/usr/bin/env bash
# Sync the local default branch with origin at session start, or report why not.
#
# A SessionStart hook implementing rules/sync-before-work.md as a deterministic
# check: it fetches origin, and if the local default branch trails
# origin/<default>, injects a short "sync before working" notice via
# additionalContext. The rule says fetch + sync before reading/editing, but
# relied on the agent remembering; we hit stale-checkout ("main is behind")
# repeatedly. This surfaces it the moment a session opens.
#
# Design choices:
#   - It DOES something (fetches + compares refs), it does not re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn tax.
#   - Fetches every session: an unverified answer would only hand the agent the
#     fetch this hook can run in seconds. A failed fetch reports "sync not
#     verified" rather than a conclusion drawn from a possibly stale
#     remote-tracking ref (rules/sync-before-work.md).
#   - The fetch is time-bounded (timeout/gtimeout, else git's HTTP low-speed and ssh
#     connect/keepalive limits) so a hung network can't
#     stall session start.
#   - Acts when the answer is mechanical: a default branch strictly behind
#     origin after a fresh fetch is fast-forwarded (git refuses every unsafe
#     case); diverged and unverified sessions are reported only.
#   - Never blocks (always exits 0), never exits 2.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read (the script needs none of it).
#   stdout: once the repo's default branch is resolved, one JSON object
#           {"additionalContext": "<status>"} whose text begins with the
#           "Session-start status — " marker (rules/hook-action-reporting.md) —
#           reporting in-sync, ahead, fast-forwarded, behind (fast-forward refused),
#           or diverged after a fresh fetch, or "sync not verified" when the
#           fetch failed.
#   exit  : always 0. Every best-effort failure emits an actionable stderr warning
#           and continues/no-ops (rules/error-handling.md Shell Error Handling).
#           Non-repo, no origin, and no local default branch are silent no-ops —
#           the sync check does not apply, so those sessions stay quiet.
#   state : none.
#   env   : SYNC_FETCH_TIMEOUT (default 10s).
set -euo pipefail

warn() { printf 'check-git-sync: %s\n' "$1" >&2; }

# Does a ref exist? `git show-ref --verify --quiet` exits 0 (exists) or 1 (the
# expected "absent / malformed name" no-result); any other exit is a real git
# failure — surface it and treat the ref as absent so best-effort work continues
# visibly (rules/error-handling.md — distinguish a non-result from a tool error).
ref_exists() { # <fully-qualified-ref>
  local rc=0
  git show-ref --verify --quiet "$1" || rc=$?
  if (( rc == 0 )); then return 0; fi
  if (( rc != 1 )); then
    warn "git show-ref failed (exit ${rc}) checking $1 — treating the ref as absent"
  fi
  return 1
}

# Emit the sync notice as additionalContext JSON. jq is required only here; its
# absence is an expected environment condition, not a failure.
emit_notice() { # <notice-text>
  # jq first, then python3; printed only once a tool produced it.
  local out
  if command -v jq >/dev/null 2>&1 && out="$(jq -n --arg c "$1" '{additionalContext: $c}')"; then
    printf '%s\n' "$out"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && out="$(python3 -c 'import json, sys; print(json.dumps({"additionalContext": sys.argv[1]}))' "$1")"; then
    printf '%s\n' "$out"
    return 0
  fi
  warn "neither jq nor python3 could encode the sync notice — install one of them; it was: ${1}"
  return 0
}

# Count the local default branch's drift from its remote-tracking ref, as the
# refs stand now (no fetch). --left-right --count on a three-dot range yields
# "<ahead>\t<behind>" — ahead = local-only commits, behind = origin-only
# commits — so a diverged branch (both > 0) can be distinguished from one that
# is strictly behind (fast-forwardable). Sets DRIFT_AHEAD / DRIFT_BEHIND and
# returns 0; a missing origin ref or a rev-list failure is warned and returns 1,
# never swallowed as "up to date".
drift_counts() { # <default-branch>
  local db="$1" counts
  if ! counts="$(git rev-list --left-right --count "refs/heads/${db}...refs/remotes/origin/${db}" 2>/dev/null)"; then
    warn "could not compare ${db} against origin/${db} — run \`git status\` to inspect; skipping sync check"
    return 1
  fi
  DRIFT_AHEAD="${counts%%[[:space:]]*}"
  DRIFT_BEHIND="${counts##*[[:space:]]}"
  if ! [[ "$DRIFT_AHEAD" =~ ^[0-9]+$ && "$DRIFT_BEHIND" =~ ^[0-9]+$ ]]; then
    warn "unexpected ahead/behind counts '${counts}' for ${db} — run \`git status\` to inspect; skipping sync check"
    return 1
  fi
  return 0
}

main() {
  local FETCH_TIMEOUT="${SYNC_FETCH_TIMEOUT:-10}"
  local rc db inside cand ahead behind
  local -a fetch

  # git is required to produce a signal; its absence is an expected environment
  # condition, not a failure — warn and no-op.
  command -v git >/dev/null 2>&1 || { warn "git not found — install git to enable the sync check"; return 0; }

  # Outside a work tree there is nothing to sync. `--is-inside-work-tree` prints
  # true/false and exits 0 inside any repo; its only failure is exit 128 ("not a
  # git repository") — the expected non-repo session. Proceed only on a literal
  # "true"; a bare repo / gitdir ("false") and the non-repo case are silent
  # no-ops, and an unexpected value is surfaced.
  inside="$(git rev-parse --is-inside-work-tree 2>/dev/null)" || inside="__notrepo__"
  case "$inside" in
    true) : ;;
    false|__notrepo__) return 0 ;;
    *) warn "unexpected \`git rev-parse --is-inside-work-tree\` output '${inside}' — skipping sync check"; return 0 ;;
  esac

  # No origin remote => nothing to compare against. `git remote get-url` exits 2
  # for the documented "No such remote" (silent no-op); any other non-zero is a
  # real git failure and is surfaced (rules/error-handling.md).
  rc=0
  git remote get-url origin >/dev/null 2>&1 || rc=$?
  if (( rc != 0 )); then
    if (( rc == 2 )); then return 0; fi
    warn "git remote get-url origin failed (exit ${rc}) — run \`git remote -v\` to inspect; skipping sync check"
    return 0
  fi

  # Resolve the remote default branch. Primary path: origin/HEAD's symbolic ref
  # (set at clone time). `git symbolic-ref --quiet` exits 0 when resolved and 1
  # for the expected no-symref case (origin/HEAD absent or not symbolic); any
  # other exit is a real git failure and is surfaced, not swallowed as "no
  # default" (rules/error-handling.md — distinguish an expected non-result from a
  # tool failure).
  db=""
  if db="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"; then
    db="${db#origin/}"
  else
    rc=$?
    db=""
    if (( rc != 1 )); then
      warn "git symbolic-ref failed (exit ${rc}) resolving origin/HEAD — falling back to name probe"
    fi
  fi
  # Fallback: probe the conventional names among the remote-tracking refs we have.
  if [[ -z "$db" ]]; then
    for cand in main master; do
      if ref_exists "refs/remotes/origin/$cand"; then db="$cand"; break; fi
    done
  fi
  if [[ -z "$db" ]]; then
    warn "cannot determine origin's default branch — set it with \`git remote set-head origin --auto\`; skipping sync check"
    return 0
  fi

  # No local default branch (e.g. only feature branches checked out) => nothing to
  # report as "behind". Silent no-op when absent; ref_exists surfaces a real
  # git failure before returning "absent".
  ref_exists "refs/heads/$db" || return 0

  # Repo hooks are off for the fetch: updating refs runs
  # `reference-transaction`, and session start must never run repo code.
  # Time-bound the fetch so a hung network can't stall session start:
  # timeout/gtimeout when present, else git's own HTTP low-speed limit and ssh
  # connect/keepalive timeouts.
  # Zero or a non-number would switch the bound off; fall back to the default.
  [[ "$FETCH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || FETCH_TIMEOUT=10
  if command -v timeout >/dev/null 2>&1; then
    fetch=(timeout "$FETCH_TIMEOUT" git -c core.hooksPath=/dev/null fetch --quiet origin)
  elif command -v gtimeout >/dev/null 2>&1; then
    fetch=(gtimeout "$FETCH_TIMEOUT" git -c core.hooksPath=/dev/null fetch --quiet origin)
  else
    fetch=(env "GIT_SSH_COMMAND=${GIT_SSH_COMMAND:-ssh} -o ConnectTimeout=${FETCH_TIMEOUT} -o ServerAliveInterval=5 -o ServerAliveCountMax=2"
      git -c core.hooksPath=/dev/null -c http.lowSpeedLimit=1000 -c "http.lowSpeedTime=${FETCH_TIMEOUT}" fetch --quiet origin)
  fi
  # A fetch failure (offline, auth, timeout) is a no-op, not a broken session —
  # warn and report unverified sync. Comparing against
  # a remote-tracking ref no fetch refreshed would risk a false "in sync"
  # (rules/sync-before-work.md Staleness Poisons Conclusions).
  if ! "${fetch[@]}" 2>/dev/null; then
    warn "git fetch origin failed or timed out — check connectivity; cannot verify sync against a current origin/${db}"
    emit_notice "Session-start status — git: \`${db}\` sync not verified against a current \`origin/${db}\` this session — run \`git fetch origin\`, then \`git status\` (rules/sync-before-work.md)."
    return 0
  fi

  drift_counts "$db" || return 0
  ahead="${DRIFT_AHEAD}"
  behind="${DRIFT_BEHIND}"

  # Success path: nothing to pull from origin. Distinguish truly in-sync from
  # ahead-only — a branch ahead of origin (unpushed commits) is not "in sync".
  if (( behind == 0 )); then
    if (( ahead == 0 )); then
      emit_notice "Session-start status — git: local \`${db}\` is in sync with \`origin/${db}\`"
    else
      emit_notice "Session-start status — git: local \`${db}\` is ${ahead} commit(s) ahead of \`origin/${db}\` (unpushed), none behind"
    fi
    return 0
  fi

  # Diverged needs judgment (which side wins), so it stays a report.
  if (( ahead > 0 )); then
    emit_notice "Session-start status — Local \`${db}\` has diverged from \`origin/${db}\` (${behind} behind, ${ahead} ahead) — reconcile before working (rules/sync-before-work.md): \`git fetch origin\`, then rebase \`${db}\` onto \`origin/${db}\` (a fast-forward won't apply)."
    return 0
  fi

  # Strictly behind after a fresh fetch: sync it rather than asking someone to.
  if fast_forward "$db"; then
    emit_notice "Session-start status — git: fast-forwarded local \`${db}\` by ${behind} commit(s) to \`origin/${db}\`"
  else
    emit_notice "Session-start status — Local \`${db}\` is ${behind} commit(s) behind \`origin/${db}\` and the automatic fast-forward was refused (see the warning above) — sync before working (rules/sync-before-work.md): fast-forward \`${db}\` to \`origin/${db}\`."
  fi
  return 0
}

# Fast-forward the local default branch to origin's, never anything else.
#
# Both paths let git refuse every unsafe case: `merge --ff-only` on the checked-out
# branch refuses a non-fast-forward and any change that would overwrite local
# work; `fetch . <src>:<dst>` for a branch not checked out here only
# fast-forwards and refuses a branch checked out in any worktree. 0 = moved,
# 1 = refused (warned).
#
# Repo hooks are disabled for both (`core.hooksPath=/dev/null`): a merge runs
# `post-merge`, and session start must not become a path that runs repo code.
fast_forward() { # <default-branch>
  local db="$1" current="" out rc=0
  # `symbolic-ref --quiet` exits 1 for a detached HEAD, the one expected
  # non-result; any other failure is a git error and refuses the fast-forward.
  current="$(git symbolic-ref --quiet --short HEAD)" || rc=$?
  if (( rc > 1 )); then
    warn "git symbolic-ref HEAD failed (exit ${rc}) — cannot tell which branch is checked out, so ${db} was not fast-forwarded"
    return 1
  fi
  rc=0
  if [[ "$current" == "$db" ]]; then
    out="$(git -c core.hooksPath=/dev/null merge --ff-only --quiet "refs/remotes/origin/${db}" 2>&1)" || rc=$?
  else
    out="$(git -c core.hooksPath=/dev/null fetch --quiet . "refs/remotes/origin/${db}:refs/heads/${db}" 2>&1)" || rc=$?
  fi
  if (( rc != 0 )); then
    warn "fast-forward of ${db} refused (exit ${rc}): ${out//$'\n'/ }"
    return 1
  fi
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts): run only when
# executed, so the script can also be sourced to unit-test its functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
