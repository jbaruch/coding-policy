#!/usr/bin/env bash
# Warn at session start when the local default branch is behind origin.
#
# A SessionStart hook implementing rules/sync-before-work.md as a deterministic
# check: it fetches origin (throttled), and if the local default branch trails
# origin/<default>, injects a short "sync before working" notice via
# additionalContext. The rule says fetch + sync before reading/editing, but
# relied on the agent remembering; we hit stale-checkout ("main is behind")
# repeatedly. This surfaces it the moment a session opens.
#
# Design choices, shared with hooks/check-policy-freshness.sh:
#   - It DOES something (fetches + compares refs), it does not re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn tax.
#   - Throttled: the fetch runs at most once per SYNC_THROTTLE_HOURS (default 1h)
#     per repo, so rapid session churn doesn't hammer the network. The behind
#     comparison still runs on throttled sessions against the last-fetched
#     origin ref, so staleness surfaces even when the fetch is skipped.
#   - The fetch is time-bounded (timeout, if available) so a hung network can't
#     stall session start.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read (the script needs none of it).
#   stdout: on a fire, one JSON object {"additionalContext": "<notice>"}; else nothing.
#   exit  : always 0. Every best-effort failure emits an actionable stderr warning
#           and continues/no-ops (rules/error-handling.md Shell Error Handling).
#           Non-repo, no origin, no local default branch, and up-to-date are all
#           silent no-ops — the common, uninteresting cases stay quiet.
#   state : $SYNC_STATE_DIR/sync-<repo-key> (default ${TMPDIR:-/tmp}/coding-policy-sync),
#           a per-repo throttle stamp (keyed by toplevel path). Schema documented
#           in hooks/state-schema.md: one line "<schema_version> <checked_at>".
#   env   : SYNC_THROTTLE_HOURS (default 1), SYNC_FETCH_TIMEOUT (default 10s),
#           SYNC_STATE_DIR (tests), SYNC_NOW (test-only injected clock; defaults
#           to `date +%s`).
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
  command -v jq >/dev/null 2>&1 || { warn "jq not found — install jq to emit the sync notice"; return 0; }
  jq -n --arg c "$1" '{additionalContext: $c}' ||
    warn "could not emit the sync notice as JSON — skipping sync check"
  return 0
}

main() {
  local THROTTLE_HOURS="${SYNC_THROTTLE_HOURS:-1}"
  local FETCH_TIMEOUT="${SYNC_FETCH_TIMEOUT:-10}"
  local STATE_DIR="${SYNC_STATE_DIR:-${TMPDIR:-/tmp}/coding-policy-sync}"
  local rc db inside cand now top repo_key stamp sv ts should_fetch counts ahead behind notice
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

  if ! [[ "$THROTTLE_HOURS" =~ ^[0-9]+$ ]]; then
    warn "SYNC_THROTTLE_HOURS='${THROTTLE_HOURS}' is not an integer — using 1"
    THROTTLE_HOURS=1
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

  # Resolve the clock. A test may inject SYNC_NOW; otherwise read the system clock
  # and handle its failure. Validate as an integer before any arithmetic so a
  # malformed value can't abort the hook under set -e.
  if [[ -n "${SYNC_NOW:-}" ]]; then
    now="$SYNC_NOW"
  elif ! now="$(date +%s)"; then
    warn "cannot read the system clock — skipping sync check"
    return 0
  fi
  if ! [[ "$now" =~ ^[0-9]+$ ]]; then
    warn "clock value '${now}' is not an integer — unset SYNC_NOW; skipping sync check"
    return 0
  fi

  # Per-repo throttle stamp, keyed by the toplevel path so sibling clones throttle
  # independently. cksum gives a stable filename-safe key for an arbitrary path.
  if ! top="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    warn "git rev-parse --show-toplevel failed — keying the throttle stamp on \$PWD instead"
    top="$PWD"
  fi
  # A cksum/cut failure would abort the assignment under set -e and break the
  # always-exit-0 contract, so handle it: fall back to an un-throttled run.
  if ! repo_key="$(printf '%s' "$top" | cksum | cut -d' ' -f1)"; then
    warn "could not derive a throttle key for ${top} — sync check will not throttle this run"
    repo_key=""
  fi
  stamp="${STATE_DIR}/sync-${repo_key}"

  # Throttle stamp schema (see hooks/state-schema.md): one line "<schema_version>
  # <checked_at-epoch>". A record whose version this hook does not accept (an old
  # bare-integer stamp, or a future version) is treated as no usable prior state
  # — re-fetch — never migrated in place (rules/stateful-artifacts.md).
  # With no throttle key the fallback fetches unconditionally (never throttles).
  should_fetch=1
  if [[ -n "$repo_key" && -r "$stamp" ]]; then
    sv=""; ts=""
    read -r sv ts < "$stamp" || { sv=""; ts=""; }
    if [[ "$sv" == "1" && "$ts" =~ ^[0-9]+$ ]] && (( now - ts < THROTTLE_HOURS * 3600 )); then
      should_fetch=0
    fi
  fi

  if (( should_fetch )); then
    # Record the fetch up front so a slow/failed fetch still throttles the next
    # session rather than retrying the network on every start. Skipped when no
    # throttle key was derived.
    if [[ -n "$repo_key" ]]; then
      if mkdir -p "$STATE_DIR"; then
        printf '1 %s\n' "$now" > "$stamp" ||
          warn "cannot write throttle stamp ${stamp} — check permissions on ${STATE_DIR}; will re-fetch next session"
      else
        warn "cannot create state dir ${STATE_DIR} — check permissions or set SYNC_STATE_DIR; sync check will not throttle"
      fi
    fi

    # Time-bound the fetch so a hung network can't stall session start. timeout is
    # optional (gtimeout on macOS via coreutils); fall back to a plain fetch.
    fetch=(git fetch --quiet origin)
    if [[ "$FETCH_TIMEOUT" =~ ^[0-9]+$ ]]; then
      if command -v timeout >/dev/null 2>&1; then
        fetch=(timeout "$FETCH_TIMEOUT" "${fetch[@]}")
      elif command -v gtimeout >/dev/null 2>&1; then
        fetch=(gtimeout "$FETCH_TIMEOUT" "${fetch[@]}")
      fi
    fi
    # A fetch failure (offline, auth, timeout) is a no-op, not a broken session —
    # warn and fall through to compare against the last-known origin ref.
    "${fetch[@]}" 2>/dev/null ||
      warn "git fetch origin failed or timed out — check connectivity; comparing against the last-fetched origin/${db}"
  fi

  # Compare the local default branch against its remote-tracking ref. --left-right
  # --count on a three-dot range yields "<ahead>\t<behind>" — ahead = local-only
  # commits, behind = origin-only commits — so a diverged branch (both > 0) can be
  # distinguished from one that is strictly behind (fast-forwardable). A missing
  # origin ref or a rev-list failure is surfaced, not swallowed as "up to date".
  if ! counts="$(git rev-list --left-right --count "refs/heads/${db}...refs/remotes/origin/${db}" 2>/dev/null)"; then
    warn "could not compare ${db} against origin/${db} — run \`git status\` to inspect; skipping sync check"
    return 0
  fi
  ahead="${counts%%[[:space:]]*}"
  behind="${counts##*[[:space:]]}"
  if ! [[ "$ahead" =~ ^[0-9]+$ && "$behind" =~ ^[0-9]+$ ]]; then
    warn "unexpected ahead/behind counts '${counts}' for ${db} — run \`git status\` to inspect; skipping sync check"
    return 0
  fi
  (( behind > 0 )) || return 0

  if (( ahead > 0 )); then
    notice="Local \`${db}\` has diverged from \`origin/${db}\` (${behind} behind, ${ahead} ahead) — reconcile before working (rules/sync-before-work.md): \`git fetch origin\`, then rebase \`${db}\` onto \`origin/${db}\` (a fast-forward won't apply)."
  else
    notice="Local \`${db}\` is ${behind} commit(s) behind \`origin/${db}\` — sync before working (rules/sync-before-work.md): \`git fetch origin\`, then fast-forward \`${db}\` to \`origin/${db}\`."
  fi

  emit_notice "$notice"
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts): run only when
# executed, so the script can also be sourced to unit-test its functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
