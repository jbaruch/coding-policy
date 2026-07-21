#!/usr/bin/env bash
# Review ONE fleet pull request from the central reviewer runner: fetch the PR
# head via the App token, install the policy, run Codex against the diff, guard
# the Codex credential, and post the verdict back as the App.
#
# Token isolation (rules/no-secrets.md): the App token is passed to git only via
# a transient `-c http.extraheader` auth header (never in the clone URL, so a
# clone/fetch failure that echoes the URL cannot leak it, and `git -c` is not
# persisted to the checkout's config), and it is unset from the environment
# before the sandbox-bypassed `codex exec` runs — so a prompt-injected PR cannot
# exfiltrate the fleet-wide App token through the review. The Codex auth.json
# stays on disk (Codex needs it) and is guarded by assert-no-secret-leak.sh.
#
# Args: <owner> <repo> <pr-number> <base-ref>
# Env:
#   GH_TOKEN     App installation token (used to fetch + to post)
#   CENTRAL_DIR  path to this coding-policy checkout (holds .github/codex-review/*)
#   CODEX_HOME   Codex home with a written+masked auth.json (default: $HOME/.codex)
#   codex, tessl, gh, jq on PATH
# Out:  the post-review.sh JSON object on stdout on success
# Exit: 0 on a posted review; non-zero with a stderr diagnostic.
set -euo pipefail

# `WORK` (throwaway PR checkout) and `POLICY` (non-workspace tessl install) are
# script-global (not main() locals) so the EXIT trap can still see them after
# main returns; guarded with :- so a trap firing before they are set is safe.
# Ends with `return 0` so a failed removal never rewrites the exit status
# (rules/error-handling.md Shell Error Handling).
WORK=""
POLICY=""
cleanup() {
  local d
  for d in "${WORK:-}" "${POLICY:-}"; do
    [[ -n "$d" ]] || continue
    rm -rf "$d" || echo "fleet-review-one.sh: warning: could not remove temp dir ${d} — remove it by hand" >&2
  done
  return 0
}

main() {
  [[ $# -eq 4 ]] || { echo "usage: $0 <owner> <repo> <pr-number> <base-ref>" >&2; exit 2; }
  local owner="$1" repo="$2" pr="$3" base="$4"
  local full="${owner}/${repo}"
  : "${GH_TOKEN:?GH_TOKEN (App installation token) required}"
  : "${CENTRAL_DIR:?CENTRAL_DIR (coding-policy checkout path) required}"
  local codex_home="${CODEX_HOME:-$HOME/.codex}"

  # fleet-prompt.md / fleet-schema.json instruct Codex to read the tessl-installed
  # policy at .tessl/plugins/jbaruch/coding-policy/rules/, which is what a reviewed
  # consumer checkout has. (This repo's own prompt.md/schema.json are the SELF-review
  # pair that reads in-tree rules/ — wrong for reviewing consumers.)
  local schema="${CENTRAL_DIR}/.github/codex-review/fleet-schema.json"
  local prompt="${CENTRAL_DIR}/.github/codex-review/fleet-prompt.md"
  # Mechanical drivers (policy-path-agnostic) are shared with the self-review.
  local assert="${CENTRAL_DIR}/.github/codex-review/assert-no-secret-leak.sh"
  local poster="${CENTRAL_DIR}/.github/codex-review/post-review.sh"
  local f
  for f in "$schema" "$prompt" "$assert" "$poster"; do
    [[ -f "$f" ]] || { echo "error: central driver file missing: $f" >&2; exit 1; }
  done

  WORK=$(mktemp -d) || { echo "error: mktemp -d failed" >&2; exit 1; }
  trap cleanup EXIT
  local work="$WORK"

  # Auth to GitHub via a transient HTTP header (git-level `-c`, applied per command
  # and NOT written to the checkout's config), with a tokenless clone URL. A
  # clone/fetch failure that echoes the URL therefore cannot leak the token, and
  # the untrusted review step later finds no token in the workdir (rules/no-secrets.md).
  local authhdr
  authhdr="Authorization: Basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
  git -c "http.https://github.com/.extraheader=${authhdr}" clone --quiet --no-tags "https://github.com/${full}.git" "$work" \
    || { echo "error: clone failed for ${full}" >&2; exit 1; }
  (
    cd "$work"
    git -c "http.https://github.com/.extraheader=${authhdr}" fetch --quiet --no-tags origin "${base}" \
      || { echo "error: could not fetch base ${base} in ${full}" >&2; exit 1; }
    git -c "http.https://github.com/.extraheader=${authhdr}" fetch --quiet --no-tags origin "pull/${pr}/head:pr-head" \
      || { echo "error: could not fetch pull/${pr}/head in ${full}" >&2; exit 1; }
    git checkout --quiet pr-head
    # Review the live PR head — GitHub records the posted review's commit_id as the
    # PR head at post time, so reviewing the live head keeps review and dedup key
    # consistent. Log exactly which SHA was reviewed.
    echo "reviewing ${full}#${pr} at $(git rev-parse HEAD)" >&2
  ) || exit 1

  # Install the policy to a NON-workspace path (rules/dependency-management.md — CI
  # agents install Tessl plugins outside the workspace, never vendored into it),
  # then expose it at the relative .tessl path the shared prompt expects via a
  # symlink, so the plugin content itself never lands in the reviewed checkout.
  POLICY=$(mktemp -d) || { echo "error: mktemp -d failed (policy)" >&2; exit 1; }
  ( cd "$POLICY" && tessl install jbaruch/coding-policy >/dev/null ) \
    || { echo "error: tessl install jbaruch/coding-policy failed for ${full}#${pr}" >&2; exit 1; }
  ln -s "${POLICY}/.tessl" "${work}/.tessl" \
    || { echo "error: could not link the installed policy into the workspace for ${full}#${pr}" >&2; exit 1; }

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
