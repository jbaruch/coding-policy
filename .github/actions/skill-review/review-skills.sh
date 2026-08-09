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
#   EVENT_BEFORE    github.event.before — a routing signal only (its zero/empty
#                   sentinel means "initial push, review all"); never a review
#                   base, since a push is not a publish boundary (#271)
#   PUBLISH_MARKER_PATTERN  ERE for the publish-bump commit subject (optional;
#                   the last-publish marker is preferred over EVENT_BEFORE — #271)
#   PUBLISH_MARKER_AUTHOR  git --author regex the marker commit must match
#                   (optional; provenance guard, default the CI bot)
#   CREDIT_OUTAGE   fail | skip                        (default fail)
#   WORKSPACE       tessl workspace, or empty to derive it from the
#                   consumer's plugin manifest name (<workspace>/<plugin>).
#                   Deriving needs `jq`; setting this skips the parse.
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
WORKSPACE="${WORKSPACE:-}"
MANIFEST="${MANIFEST:-.tessl-plugin/plugin.json}"
LEGACY_MANIFEST="${LEGACY_MANIFEST:-tile.json}"
# The commit tesslio/patch-version-publish pushes after a green review+publish
# ("Bump <name> to X.Y.Z [skip ci]"). identify_skills diffs from the nearest
# such ancestor rather than github.event.before, so a publish that FAILED at or
# after the review step does not drop its changed skills from the next run's
# window (#271).
#
# The marker must be an AUTHORITATIVE publish boundary, not merely a commit whose
# subject matches — a look-alike message on a merged feature branch, or a
# hand-authored commit, must never move the base past unreviewed skills. Two
# guards enforce provenance (resolve_publish_marker):
#   - --first-parent: only the protected branch's mainline, never a commit
#     carried in by a merged feature branch;
#   - --author PUBLISH_MARKER_AUTHOR: the CI bot that publishes, so a
#     hand-authored look-alike on mainline is excluded too.
# PUBLISH_MARKER_PATTERN is an extended-regexp matched against the commit
# SUBJECT (not the whole message, so a body line cannot false-match);
# PUBLISH_MARKER_AUTHOR is a git --author regex. Override both for a publish flow
# with a different marker (a consumer can always pass base-ref).
PUBLISH_MARKER_PATTERN="${PUBLISH_MARKER_PATTERN:-^Bump .+ to [0-9]+\.[0-9]+\.[0-9]+.*\[skip ci\]}"
PUBLISH_MARKER_AUTHOR="${PUBLISH_MARKER_AUTHOR:-github-actions\[bot\]}"

# Skills skipped because the tessl org was out of credits. Populated by
# run_reviews; read by main for the action output.
UNREVIEWED=()

# The workspace that `tessl review run --workspace` requires.
#
# The flag is mandatory in the tessl CLI and every call here omitted it, so the
# action died with "Missing required flag: --workspace" the first time a
# consumer actually changed a skill. It stayed latent because a run with no
# changed skills never reaches the review call, and most publishes change no
# skill.
#
# Derived from the consumer's own manifest rather than configured: a plugin
# `name` is `<workspace>/<plugin>`, so the workspace is already declared there,
# and a second place to state it is a second place for it to drift. The
# WORKSPACE input overrides for a consumer whose review workspace legitimately
# differs from its publish workspace.
resolve_workspace() {
  if [ -n "$WORKSPACE" ]; then
    printf '%s' "$WORKSPACE"
    return 0
  fi

  if ! command -v jq >/dev/null; then
    echo "::error::Deriving the tessl workspace from the plugin manifest needs \`jq\`, which is not on PATH. Install it (ubuntu: apt-get install -y jq, macOS: brew install jq), or set the action's \`workspace\` input — that skips the manifest parse entirely." >&2
    return 2
  fi

  local manifest=""
  if [ -f "$MANIFEST" ]; then
    manifest="$MANIFEST"
  elif [ -f "$LEGACY_MANIFEST" ]; then
    manifest="$LEGACY_MANIFEST"
  else
    echo "::error::Cannot resolve the tessl workspace: neither ${MANIFEST} nor ${LEGACY_MANIFEST} exists. Set the action's \`workspace\` input." >&2
    return 2
  fi

  local name=""
  if ! name="$(jq -r '.name // empty' "$manifest")"; then
    echo "::error::Cannot read ${manifest} as JSON. Fix the manifest, or set the action's \`workspace\` input." >&2
    return 2
  fi
  case "$name" in
    */*) printf '%s' "${name%%/*}" ;;
    *)
      echo "::error::${manifest} has name '${name}', expected '<workspace>/<plugin>'. Fix the manifest, or set the action's \`workspace\` input." >&2
      return 2
      ;;
  esac
}

# True only when review output carries an out-of-credits billing signature.
# Precision matters: this is the sole failure the skip mode tolerates, so a
# too-loose match would publish a genuinely-broken skill unreviewed. Two
# accepted signatures:
#   1. Legacy CLI: the credit phrase AND an explicit 403 status line,
#      each a fixed string (grep -F).
#   2. Current CLI (emits no status code): the FULL billing sentence as
#      an entire output line — anchored `^...$`, tolerating only a
#      leading non-alphanumeric glyph/whitespace prefix (the CLI's `✘ `
#      marker; a digit prefix like a numbered list is NOT tolerated). A
#      real failure that quotes the sentence mid-line, a partial phrase,
#      or a future wording change all fall through to hard-fail (the
#      safe direction). The sentence's dots are escaped; no other regex
#      metacharacters appear in it.
# tessl exposes no distinct exit code for the billing failure; if it ever
# does, prefer that over string matching.
is_credit_outage() {
  if printf '%s' "$1" | grep -qiF 'run out of credits' \
    && printf '%s' "$1" | grep -qF '403'; then
    return 0
  fi
  printf '%s' "$1" | grep -qE '^[^[:alnum:]]*Your organization has run out of credits\. Upgrade your plan or buy more credits to continue\.$'
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
  echo "::warning::tessl org out of credits — skipping review for '${skill}'. Publishing UNREVIEWED; review resumes by ${resume} (monthly credit reset)."
  {
    echo "### ⚠️ Skill review skipped — tessl credits exhausted"
    echo ""
    echo "\`${skill}\` was **not** reviewed: the tessl org is out of credits."
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

  # Resolved once, before any review: a workspace that cannot be resolved is a
  # setup error, and discovering it per-skill would report it as a review
  # failure for whichever skill happened to be first.
  local workspace
  workspace="$(resolve_workspace)" || return 2

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
    review_out="$(tessl review run --workspace "$workspace" --threshold "$THRESHOLD" "$SKILLS_DIR/$skill/SKILL.md" 2>&1)" || rc=$?
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

# Emit the SHA of the nearest first-parent, CI-bot-authored commit whose SUBJECT
# matches PUBLISH_MARKER_PATTERN over the clone's current history, or empty
# stdout if none. The subject is matched per commit rather than via `git log
# --grep` (which searches the whole message), so a matching line in a commit's
# BODY can never make a non-publish commit the marker. Returns non-zero with an
# actionable ::error:: on a git/regex failure (an invalid pattern, a broken
# clone) so the caller can tell "no marker" from "tool failure".
_marker_log() {
  local out rc grc errfile sha subject

  # Validate the ERE once up front (against empty input) so an invalid pattern
  # is a loud error even when no candidate commit exists — never a silent
  # narrow. The 2>/dev/null is paired with an explicit exit-2 handler here, not
  # a stand-in for one (grep: 0 match, 1 no-match, 2 bad regex).
  grc=0
  printf '' | grep -qE "$PUBLISH_MARKER_PATTERN" 2>/dev/null || grc=$?
  if [ "$grc" -eq 2 ]; then
    echo "::error::resolve_publish_marker: invalid PUBLISH_MARKER_PATTERN='${PUBLISH_MARKER_PATTERN}' (not a valid extended regexp)." >&2
    return 1
  fi

  # %H<TAB>%s per first-parent, author-matched commit, nearest first.
  errfile="$(mktemp)"; rc=0
  out="$(git log --first-parent --author="$PUBLISH_MARKER_AUTHOR" \
           --format='%H%x09%s' HEAD 2>"$errfile")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "::error::resolve_publish_marker: \`git log\` failed (exit ${rc}). Check PUBLISH_MARKER_AUTHOR='${PUBLISH_MARKER_AUTHOR}'. git: $(tr '\n' ' ' <"$errfile")" >&2
    rm -f "$errfile"
    return 1
  fi
  rm -f "$errfile"

  # First commit whose SUBJECT matches (pattern already validated above).
  while IFS=$'\t' read -r sha subject; do
    [ -z "$sha" ] && continue
    if printf '%s' "$subject" | grep -qE "$PUBLISH_MARKER_PATTERN"; then
      printf '%s' "$sha"
      return 0
    fi
  done <<< "$out"
  return 0
}

# Exit contract (identify_skills branches on it):
#   0 + SHA on stdout  — marker found
#   0 + empty stdout   — no marker in COMPLETE history (first publish / other
#                        publish flow); caller reviews ALL skills (fail-safe)
#   1                  — hard error (invalid pattern, broken clone); setup error
#   3                  — cannot resolve safely: a marker may exist but be
#                        unseen (the clone is shallow and could not be deepened,
#                        or its depth could not be determined). Caller reviews
#                        EVERY skill rather than narrowing to github.event.before,
#                        which could let a failed publish's skills escape review
#                        (context-artifacts Mandatory Review fail-safe).
resolve_publish_marker() {
  local sha
  # First pass over whatever history the clone already has.
  sha="$(_marker_log)" || return 1
  if [ -n "$sha" ]; then
    printf '%s' "$sha"
    return 0
  fi

  # Not found: only a genuinely shallow clone might be hiding the marker. Each
  # git call's exit is checked explicitly — never silenced in place of a handler
  # (rules/error-handling.md).
  local is_shallow shallow_rc
  is_shallow="$(git rev-parse --is-shallow-repository 2>&1)"; shallow_rc=$?
  if [ "$shallow_rc" -ne 0 ]; then
    # Cannot tell whether the clone is hiding a marker — a tool failure, not a
    # genuine "no marker". Fail SAFE: review-all, never narrow to event.before.
    echo "::warning::resolve_publish_marker: could not determine clone depth (git: ${is_shallow}); reviewing ALL skills rather than risk narrowing the window." >&2
    return 3
  fi
  if [ "$is_shallow" != "true" ]; then
    return 0   # complete history, no marker — caller reviews ALL (fail-safe)
  fi

  # Shallow and no marker yet: deepen once and re-scan.
  if git fetch --unshallow origin 2>/dev/null; then
    sha="$(_marker_log)" || return 1
    printf '%s' "$sha"
    return 0
  fi

  # Shallow AND could not be deepened: a marker may exist but is unreachable.
  # Fail SAFE — signal review-all, never narrow to github.event.before.
  echo "::warning::resolve_publish_marker: clone is shallow and could not be deepened (set fetch-depth: 0); reviewing ALL skills so an unpublished change cannot escape review." >&2
  return 3
}

# Resolve the diff base and echo the changed skill names (one per line).
# BASE_OVERRIDE wins; else "" (review every skill) for workflow_dispatch /
# initial push; else the last successful-publish marker (#271). An
# unreachable non-empty base hard-fails rather than silently collapsing to
# "no changes".
#
# github.event.before is NOT a review base. Diffing since the previous PUSH is
# the original #271 bug: a push is not a publish boundary, so a failed publish's
# skills can fall outside `event.before..HEAD` and ship unreviewed. Only the
# publish marker is an authoritative boundary; with no marker (an as-is publish
# flow, or before the first bump exists) the fail-safe is to review EVERY skill,
# never to narrow to event.before (context-artifacts Mandatory Review).
identify_skills() {
  local base
  if [ -n "$BASE_OVERRIDE" ]; then
    base="$BASE_OVERRIDE"
  elif [ "$EVENT_NAME" = "workflow_dispatch" ] \
       || [ -z "$EVENT_BEFORE" ] \
       || [ "$EVENT_BEFORE" = "0000000000000000000000000000000000000000" ]; then
    base=""
  else
    # Branch on resolve_publish_marker's exit contract (see its header):
    #   0 + SHA   → base = marker
    #   0 + empty → no authoritative marker → review ALL (base="")
    #   3         → cannot resolve safely (shallow) → review ALL (base="")
    #   other ≠0  → hard error → setup error 2
    local marker_rc=0
    base="$(resolve_publish_marker)" || marker_rc=$?
    case "$marker_rc" in
      0|3) [ "$marker_rc" -eq 3 ] && base="" ;;
      *)   return 2 ;;
    esac
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
