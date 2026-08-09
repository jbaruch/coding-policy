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
#           a single epoch-seconds throttle stamp per repo (keyed by toplevel path).
#   env   : SYNC_THROTTLE_HOURS (default 1), SYNC_FETCH_TIMEOUT (default 10s),
#           SYNC_STATE_DIR (tests), SYNC_NOW (test-only injected clock; defaults
#           to `date +%s`).
set -euo pipefail

THROTTLE_HOURS="${SYNC_THROTTLE_HOURS:-1}"
FETCH_TIMEOUT="${SYNC_FETCH_TIMEOUT:-10}"
STATE_DIR="${SYNC_STATE_DIR:-${TMPDIR:-/tmp}/coding-policy-sync}"

warn() { printf 'check-git-sync: %s\n' "$1" >&2; }

# git is required to produce a signal; its absence is an expected environment
# condition, not a failure — warn and no-op.
command -v git >/dev/null 2>&1 || { warn "git not found — install git to enable the sync check"; exit 0; }

# Outside a work tree there is nothing to sync — the common non-repo session.
# Silent no-op (git prints its own error to the discarded stderr).
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# No origin remote => nothing to compare against. Silent no-op.
git remote get-url origin >/dev/null 2>&1 || exit 0

if ! [[ "$THROTTLE_HOURS" =~ ^[0-9]+$ ]]; then
  warn "SYNC_THROTTLE_HOURS='${THROTTLE_HOURS}' is not an integer — using 1"
  THROTTLE_HOURS=1
fi

# Resolve the remote default branch. Primary path: origin/HEAD's symbolic ref
# (set at clone time). Fallback: probe the conventional names among the
# remote-tracking refs we already have.
db="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
db="${db#origin/}"
if [[ -z "$db" ]]; then
  for cand in main master; do
    if git show-ref --verify --quiet "refs/remotes/origin/$cand"; then db="$cand"; break; fi
  done
fi
if [[ -z "$db" ]]; then
  warn "cannot determine origin's default branch — set it with \`git remote set-head origin --auto\`; skipping sync check"
  exit 0
fi

# No local default branch (e.g. only feature branches checked out) => nothing to
# report as "behind". Silent no-op.
git show-ref --verify --quiet "refs/heads/$db" || exit 0

# Resolve the clock. A test may inject SYNC_NOW; otherwise read the system clock
# and handle its failure. Validate as an integer before any arithmetic so a
# malformed value can't abort the hook under set -e.
if [[ -n "${SYNC_NOW:-}" ]]; then
  now="$SYNC_NOW"
elif ! now="$(date +%s)"; then
  warn "cannot read the system clock — skipping sync check"
  exit 0
fi
if ! [[ "$now" =~ ^[0-9]+$ ]]; then
  warn "clock value '${now}' is not an integer — unset SYNC_NOW; skipping sync check"
  exit 0
fi

# Per-repo throttle stamp, keyed by the toplevel path so sibling clones throttle
# independently. cksum gives a stable filename-safe key for an arbitrary path.
# A cksum/cut failure would abort the assignment under set -e and break the
# always-exit-0 contract, so handle it: fall back to an un-throttled run rather
# than crash the session.
top="$(git rev-parse --show-toplevel 2>/dev/null || printf '%s' "$PWD")"
if ! repo_key="$(printf '%s' "$top" | cksum | cut -d' ' -f1)"; then
  warn "could not derive a throttle key for ${top} — sync check will not throttle this run"
  repo_key=""
fi
stamp="${STATE_DIR}/sync-${repo_key}"

# With no throttle key the fallback fetches unconditionally (never throttles).
should_fetch=1
if [[ -n "$repo_key" && -r "$stamp" ]]; then
  last=""
  read -r last < "$stamp" || last=""
  [[ "$last" =~ ^[0-9]+$ ]] || last=0
  if (( now - last < THROTTLE_HOURS * 3600 )); then
    should_fetch=0
  fi
fi

if (( should_fetch )); then
  # Record the fetch up front so a slow/failed fetch still throttles the next
  # session rather than retrying the network on every start. Skipped when no
  # throttle key was derived.
  if [[ -n "$repo_key" ]]; then
    if mkdir -p "$STATE_DIR"; then
      printf '%s\n' "$now" > "$stamp" ||
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

# Compare the local default branch against its remote-tracking ref. A missing
# origin ref or a rev-list failure is surfaced, not swallowed as "up to date".
if ! behind="$(git rev-list --count "refs/heads/${db}..refs/remotes/origin/${db}" 2>/dev/null)"; then
  warn "could not compare ${db} against origin/${db} — run \`git status\` to inspect; skipping sync check"
  exit 0
fi
if ! [[ "$behind" =~ ^[0-9]+$ ]]; then
  warn "unexpected non-numeric behind-count '${behind}' for ${db} — run \`git status\` to inspect; skipping sync check"
  exit 0
fi
(( behind > 0 )) || exit 0

notice="Local \`${db}\` is ${behind} commit(s) behind \`origin/${db}\` — sync before working (rules/sync-before-work.md): \`git fetch origin\`, then fast-forward \`${db}\` to \`origin/${db}\`."

command -v jq >/dev/null 2>&1 || { warn "jq not found — install jq to emit the sync notice; behind by ${behind}"; exit 0; }
if ! jq -n --arg c "$notice" '{additionalContext: $c}'; then
  warn "could not emit the sync notice as JSON — skipping sync check"
  exit 0
fi
exit 0
