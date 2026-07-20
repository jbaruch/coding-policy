#!/usr/bin/env bash
# Review ONE fleet pull request from the central reviewer runner: fetch the PR
# head via the App token, install the policy, run Codex against the diff, guard
# the Codex credential, and post the verdict back as the App.
#
# Token isolation (rules/no-secrets.md): the App token fetches the code, then is
# scrubbed from the workdir git config AND unset from the environment before the
# sandbox-bypassed `codex exec` runs, so a prompt-injected PR cannot exfiltrate
# the fleet-wide App token through the review. The Codex auth.json stays on disk
# (Codex needs it) and is guarded by assert-no-secret-leak.sh as before.
#
# Args: <owner> <repo> <pr-number> <base-ref> <head-sha>
# Env:
#   GH_TOKEN     App installation token (used to fetch + to post)
#   CENTRAL_DIR  path to this coding-policy checkout (holds .github/codex-review/*)
#   CODEX_HOME   Codex home with a written+masked auth.json (default: $HOME/.codex)
#   codex, tessl, gh, jq on PATH
# Out:  the post-review.sh JSON object on stdout on success
# Exit: 0 on a posted review; non-zero with a stderr diagnostic.
set -euo pipefail

# `WORK` is script-global (not a main() local) so the EXIT trap can still see it
# after main returns; guarded with :- so a trap firing before it is set is safe.
# Ends with `return 0` so a failed removal never rewrites the exit status
# (rules/error-handling.md Shell Error Handling).
WORK=""
cleanup() {
  [[ -n "${WORK:-}" ]] || return 0
  rm -rf "$WORK" || echo "fleet-review-one.sh: warning: could not remove temp dir ${WORK} — remove it by hand" >&2
  return 0
}

main() {
  [[ $# -eq 4 ]] || { echo "usage: $0 <owner> <repo> <pr-number> <base-ref>" >&2; exit 2; }
  local owner="$1" repo="$2" pr="$3" base="$4"
  local full="${owner}/${repo}"
  : "${GH_TOKEN:?GH_TOKEN (App installation token) required}"
  : "${CENTRAL_DIR:?CENTRAL_DIR (coding-policy checkout path) required}"
  local codex_home="${CODEX_HOME:-$HOME/.codex}"

  local schema="${CENTRAL_DIR}/.github/codex-review/schema.json"
  local prompt="${CENTRAL_DIR}/.github/codex-review/prompt.md"
  local assert="${CENTRAL_DIR}/.github/codex-review/assert-no-secret-leak.sh"
  local poster="${CENTRAL_DIR}/.github/codex-review/post-review.sh"
  local f
  for f in "$schema" "$prompt" "$assert" "$poster"; do
    [[ -f "$f" ]] || { echo "error: central driver file missing: $f" >&2; exit 1; }
  done

  WORK=$(mktemp -d) || { echo "error: mktemp -d failed" >&2; exit 1; }
  trap cleanup EXIT
  local work="$WORK"

  # Fetch the PR head with the token, then scrub the token from git config so it
  # is gone before the untrusted review step runs.
  git clone --quiet --no-tags "https://x-access-token:${GH_TOKEN}@github.com/${full}.git" "$work" \
    || { echo "error: clone failed for ${full}" >&2; exit 1; }
  (
    cd "$work"
    git fetch --quiet --no-tags origin "${base}" || { echo "error: could not fetch base ${base} in ${full}" >&2; exit 1; }
    git fetch --quiet --no-tags origin "pull/${pr}/head:pr-head" || { echo "error: could not fetch pull/${pr}/head in ${full}" >&2; exit 1; }
    git checkout --quiet pr-head
    git remote set-url origin "https://github.com/${full}.git"  # drop the embedded token
    # Review the live PR head — GitHub records the posted review's commit_id as the
    # PR head at post time, so reviewing the live head keeps review and dedup key
    # consistent. Log exactly which SHA was reviewed.
    echo "reviewing ${full}#${pr} at $(git rev-parse HEAD)" >&2
  ) || exit 1

  # Install the policy into the checkout so the prompt's rule paths resolve.
  ( cd "$work" && tessl install jbaruch/coding-policy >/dev/null ) \
    || { echo "error: tessl install jbaruch/coding-policy failed for ${full}#${pr}" >&2; exit 1; }

  # Run Codex against the diff with the App token REMOVED from the environment.
  local out="${work}/.codex-final.json"
  (
    cd "$work"
    # Single quotes are required: the backticks are literal markdown and the only
    # substitutions are printf's own %s — double quotes would make bash treat the
    # backticks as command substitution. Nothing here is meant to shell-expand.
    # shellcheck disable=SC2016
    { printf 'This PR targets base branch `%s`; review the diff `git diff origin/%s...HEAD`.\n\n' "$base" "$base"; cat "$prompt"; } \
      | env -u GH_TOKEN CODEX_HOME="$codex_home" codex exec \
          --json \
          --skip-git-repo-check \
          --dangerously-bypass-approvals-and-sandbox \
          --output-schema "$schema" \
          --output-last-message "$out" \
          -
  ) || { echo "error: codex exec failed for ${full}#${pr}" >&2; exit 1; }

  # Refuse to post if the output echoes the Codex credential.
  bash "$assert" "${codex_home}/auth.json" "$out"

  # Post the verdict back as the App (post-review.sh reads GH_TOKEN).
  bash "$poster" "$owner" "$repo" "$pr" "$out"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
