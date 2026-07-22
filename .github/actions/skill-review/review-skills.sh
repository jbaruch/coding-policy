#!/usr/bin/env bash
# Identify the skills whose files changed since the previous push and run
# `tessl review run --threshold N` on each, per jbaruch/coding-policy
# `context-artifacts` "Mandatory Review". Unchanged skills are not
# re-reviewed — that just burns runner time and Tessl credits.
#
# This is the executable body of the `skill-review` composite action
# (`action.yml` calls it via `bash "$GITHUB_ACTION_PATH/review-skills.sh"`).
# It lives as a real file, not an inline `run:` block, so its
# credit-outage classification is unit-testable (rules/script-delegation.md);
# tests source it and drive `run_reviews` with a mocked `tessl`.
#
# Environment contract (action.yml sets these from its inputs):
#   THRESHOLD       minimum passing score              (default 85)
#   SKILLS_DIR      dir holding skill subdirectories   (default skills)
#   BASE_OVERRIDE   explicit diff base, or empty
#   EVENT_NAME      github.event_name
#   EVENT_BEFORE    github.event.before
#   CREDIT_OUTAGE   fail | skip                        (default fail)
#   GITHUB_OUTPUT / GITHUB_STEP_SUMMARY  runner-provided sinks (optional
#                   off-runner; default to /dev/null)
#
# Out: writes `unreviewed-skills=<csv>` to $GITHUB_OUTPUT (skills skipped
#      under a credit outage; empty when all were reviewed).
# Exit: 0 when every changed skill passed review (or was credit-skipped
#       under CREDIT_OUTAGE=skip); the failing skill's exit code otherwise;
#       2 on setup error (unreachable base, invalid credit-outage value).

set -euo pipefail

THRESHOLD="${THRESHOLD:-85}"
SKILLS_DIR="${SKILLS_DIR:-skills}"
BASE_OVERRIDE="${BASE_OVERRIDE:-}"
EVENT_NAME="${EVENT_NAME:-}"
EVENT_BEFORE="${EVENT_BEFORE:-}"
CREDIT_OUTAGE="${CREDIT_OUTAGE:-fail}"

# Skills skipped because the tessl org was out of credits. Populated by
# run_reviews; read by main for the action output.
UNREVIEWED=()

# True only when review output carries an out-of-credits billing signature.
# Precision matters: this is the sole failure the skip mode tolerates, so a
# too-loose match would publish a genuinely-broken skill unreviewed. Two
# accepted signatures, each built from fixed strings (grep -F, no regex
# metacharacters):
#   1. Legacy CLI: the credit phrase AND an explicit 403 status line.
#   2. Current CLI (emits no status code): the FULL billing sentence,
#      matched whole so a real failure that merely quotes "run out of
#      credits" in prose still falls through to hard-fail (the safe
#      direction). A future wording change also hard-fails until this
#      list learns the new sentence.
# tessl exposes no distinct exit code for the billing failure; if it ever
# does, prefer that over string matching.
is_credit_outage() {
  if printf '%s' "$1" | grep -qiF 'run out of credits' \
    && printf '%s' "$1" | grep -qF '403'; then
    return 0
  fi
  printf '%s' "$1" | grep -qF 'Your organization has run out of credits. Upgrade your plan or buy more credits to continue.'
}

# Emit the operator-facing warning and run-summary note for one skill that
# went unreviewed because the tessl org is out of credits. Credits reset
# monthly on the 1st, so name the resume date to make the skip window
# legible; a non-GNU `date` falls back to a generic phrase (explicit
# fallback, not error-swallowing per rules/error-handling.md).
credit_skip() {
  local skill="$1" resume
  resume="$(date -u -d "$(date -u +%Y-%m-01) +1 month" +%Y-%m-01 2>/dev/null)" \
    || resume="the 1st of next month"
  echo "::warning::tessl org out of credits (403) — skipping review for '${skill}'. Publishing UNREVIEWED; review resumes by ${resume} (monthly credit reset)."
  {
    echo "### ⚠️ Skill review skipped — tessl credits exhausted"
    echo ""
    echo "\`${skill}\` was **not** reviewed: the tessl org is out of credits (403)."
    echo "It is publishing **without** review. The gate self-heals — top up credits to restore it immediately, otherwise it resumes by **${resume}** (monthly reset)."
  } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
}

# Review each named skill. Reads THRESHOLD, SKILLS_DIR, CREDIT_OUTAGE.
# Under CREDIT_OUTAGE=skip a review call that fails with the out-of-credits
# signature is tolerated (recorded in UNREVIEWED, publish continues); every
# other non-zero exit — a below-threshold score, an auth error, a tooling
# bug — still hard-fails, whatever the mode. Returns the failing skill's
# exit code on a hard failure, 0 when all skills were handled.
run_reviews() {
  case "$CREDIT_OUTAGE" in
    fail|skip) ;;
    *)
      echo "::error::invalid credit-outage '${CREDIT_OUTAGE}' — expected 'fail' or 'skip'." >&2
      return 2
      ;;
  esac

  UNREVIEWED=()
  local skill review_out rc
  for skill in "$@"; do
    [ -f "$SKILLS_DIR/$skill/SKILL.md" ] || { echo "  - ${skill}: deleted, skipping"; continue; }
    echo "::group::Reviewing ${skill}"
    # Capture output so an out-of-credits outage (a billing state, not a
    # skill defect) can be told apart from a real failure. `|| rc=$?`
    # captures the exit without aborting under `set -e` and without
    # toggling `-e` (which would leak to callers); never blanket-swallow —
    # only the credit signature under skip-mode is tolerated below.
    rc=0
    review_out="$(tessl review run --threshold "$THRESHOLD" "$SKILLS_DIR/$skill/SKILL.md" 2>&1)" || rc=$?
    printf '%s\n' "$review_out"
    echo "::endgroup::"
    [ "$rc" -eq 0 ] && continue
    if [ "$CREDIT_OUTAGE" = "skip" ] && is_credit_outage "$review_out"; then
      credit_skip "$skill"
      UNREVIEWED+=("$skill")
      continue
    fi
    local why=""
    [ "$CREDIT_OUTAGE" = "skip" ] && why=" — not a credits outage"
    echo "::error::Skill review failed for '${skill}' (exit ${rc})${why} — blocking publish."
    return "$rc"
  done
  return 0
}

# Resolve the diff base and echo the changed skill names (one per line).
# BASE_OVERRIDE wins; else github.event.before for push; else "" meaning
# review every skill (workflow_dispatch / initial push expect a full
# re-review). An unreachable non-empty base hard-fails rather than
# silently collapsing to "no changes".
identify_skills() {
  local base
  if [ -n "$BASE_OVERRIDE" ]; then
    base="$BASE_OVERRIDE"
  elif [ "$EVENT_NAME" = "workflow_dispatch" ] \
       || [ -z "$EVENT_BEFORE" ] \
       || [ "$EVENT_BEFORE" = "0000000000000000000000000000000000000000" ]; then
    base=""
  else
    base="$EVENT_BEFORE"
  fi

  if [ -z "$base" ]; then
    # Immediate subdir basenames, sorted. `sed 's|.*/||'` instead of GNU
    # `find -printf '%f'` keeps discovery portable (BSD find on macOS has no
    # -printf) so this path is testable off the CI runner. Skill dir names
    # are kebab-case with no newlines.
    find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||' | sort
    return 0
  fi

  # Validate the base is a real commit before relying on it. If not
  # reachable in the local clone, try a shallow fetch; if that fails too,
  # hard-fail rather than silently converting an unreachable base into
  # "no changes". The fetches are best-effort — the rev-parse check below
  # is the real gate — so a failed fetch warns rather than aborting.
  if ! git rev-parse --verify "${base}^{commit}" >/dev/null 2>&1; then
    echo "Base ${base} not reachable in clone; fetching..." >&2
    git fetch --no-tags --depth=50 origin "$base" 2>/dev/null \
      || git fetch --unshallow origin 2>/dev/null \
      || echo "::warning::could not fetch base ${base}; re-checking reachability" >&2
  fi
  if ! git rev-parse --verify "${base}^{commit}" >/dev/null 2>&1; then
    echo "::error::Base ${base} is not a reachable commit; refusing to silently skip skill review." >&2
    echo "::error::Set actions/checkout fetch-depth: 0 in the calling workflow, or pass an explicit base-ref." >&2
    return 2
  fi
  # Strip the SKILLS_DIR prefix from each diff path and pull out the first
  # path component beneath it — works for any depth (single-component
  # `skills` or nested `pkg/skills`).
  git diff --name-only "$base..HEAD" -- "$SKILLS_DIR/" \
    | awk -v prefix="$SKILLS_DIR/" '
        index($0, prefix) == 1 {
          rest = substr($0, length(prefix) + 1)
          slash = index(rest, "/")
          if (slash > 0) print substr(rest, 1, slash - 1)
        }' | sort -u
}

main() {
  # Capture-and-check rather than `mapfile < <(identify_skills)`: a process
  # substitution hides the function's non-zero return, which would drop the
  # unreachable-base hard-fail and silently review nothing.
  local skills_raw skills=() rc=0
  skills_raw="$(identify_skills)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    return "$rc"
  fi
  if [ -n "$skills_raw" ]; then
    mapfile -t skills <<< "$skills_raw"
  fi
  echo "Reviewing ${#skills[@]} skill(s) (credit-outage=${CREDIT_OUTAGE})."

  if [ "${#skills[@]}" -gt 0 ]; then
    run_reviews "${skills[@]}"
  else
    echo "No skill changes — review skipped."
  fi

  local csv=""
  if [ "${#UNREVIEWED[@]}" -gt 0 ]; then
    csv=$(IFS=,; printf '%s' "${UNREVIEWED[*]}")
  fi
  echo "unreviewed-skills=${csv}" >> "${GITHUB_OUTPUT:-/dev/null}"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
