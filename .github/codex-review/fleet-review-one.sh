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
# Set by install_manifest_deps when any trusted manifest names nanoclaw-core,
# which is materialized last in its own isolated workspace.
NEED_CORE=false
cleanup() {
  local d
  for d in "${WORK:-}" "${POLICY:-}"; do
    [[ -n "$d" ]] || continue
    rm -rf "$d" || echo "fleet-review-one.sh: warning: could not remove temp dir ${d} — remove it by hand" >&2
  done
  return 0
}

# Install every dependency one trusted manifest declares into $POLICY. Skips
# coding-policy (already installed at its latest) and records nanoclaw-core in
# NEED_CORE for the isolated materializer. A plugin that fails to install is a
# warning, not a failure; a manifest that does not parse refuses the review.
install_manifest_deps() { # <manifest-json> <label> <full> <pr>
  local manifest="$1" label="$2" full="$3" pr="$4" deps dep
  # Capture the dependency list in an explicitly checked assignment, NOT via
  # process substitution: `while read < <(jq ...)` discards jq's exit status,
  # so a malformed manifest would yield an empty list and the review would
  # proceed with no authority plugins installed (rules/error-handling.md Shell
  # Error Handling).
  if ! deps=$(printf '%s' "$manifest" | jq -r '.dependencies // {} | keys[]'); then
    echo "error: could not parse dependencies from ${label} — refusing to review with an unknown plugin set" >&2
    exit 1
  fi
  while IFS= read -r dep; do
    [ -n "$dep" ] || continue
    [ "$dep" = "jbaruch/coding-policy" ] && continue
    if [ "$dep" = "jbaruch/nanoclaw-core" ]; then
      NEED_CORE=true
      continue
    fi
    if ! ( cd "$POLICY" && tessl install "$dep" >/dev/null 2>&1 ); then
      # Best-effort per `rules/error-handling.md` Graceful Fallback: a
      # private or unpublished plugin must not fail the whole review,
      # but it MUST be visible — a silently missing authority rule is
      # how a reviewer blocks a compliant PR without saying why.
      echo "::warning::could not install ${dep} (from ${label}) for ${full}#${pr} — carve-outs citing its rules cannot be verified this run" >&2
    fi
  done <<< "$deps"
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
  local authoring="${CENTRAL_DIR}/.github/codex-review/policy-authoring-context.py"
  local history="${CENTRAL_DIR}/.github/codex-review/prior-feedback.py"
  local core_materializer="${CENTRAL_DIR}/.github/codex-review/materialize-core-authority.py"
  # Mechanical drivers (policy-path-agnostic) are shared with the self-review.
  local assert="${CENTRAL_DIR}/.github/codex-review/assert-no-secret-leak.sh"
  local runner="${CENTRAL_DIR}/.github/reviewer_failure.py"
  local poster="${CENTRAL_DIR}/.github/codex-review/post-review.sh"
  local f
  for f in "$schema" "$prompt" "$authoring" "$history" "$assert" "$poster" "$runner" "$core_materializer"; do
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
  ) || exit 1
  # Capture once before analysis. A later push must never retarget this verdict.
  local reviewed_sha
  reviewed_sha=$(cd "$work" && git rev-parse HEAD) || exit 1
  if [[ ! "$reviewed_sha" =~ ^[a-f0-9]{40}$ ]]; then
    echo 'error: checkout returned an invalid source SHA — restore git and rerun this review' >&2
    exit 1
  fi
  echo "reviewing ${full}#${pr} at ${reviewed_sha}" >&2
  local whitespace_status=clean
  if ! (cd "$work" && git diff --check "origin/${base}...HEAD" >/dev/null 2>&1); then
    whitespace_status=needs_inspection
    echo "note: whitespace preflight was nonzero for ${full}#${pr}; continue policy review and inspect applicable source rules" >&2
  fi

  local prior_feedback
  prior_feedback=$(python3 -I "$history" "$owner" "$repo" "$pr" "$reviewed_sha") || exit 1

  local authoring_context
  if ! authoring_context=$(cd "$work" && python3 -I "$authoring" --repo "$full" --base "origin/${base}"); then
    echo "error: cannot establish plugin authoring context for ${full}; restore the fetched metadata before reviewing" >&2
    exit 1
  fi

  # Install the policy to a NON-workspace path (rules/dependency-management.md — CI
  # agents install Tessl plugins outside the workspace, never vendored into it),
  # then expose it at the relative .tessl path the shared prompt expects via a
  # symlink, so the plugin content itself never lands in the reviewed checkout.
  POLICY=$(mktemp -d) || { echo "error: mktemp -d failed (policy)" >&2; exit 1; }
  ( cd "$POLICY" && tessl install jbaruch/coding-policy >/dev/null ) \
    || { echo "error: tessl install jbaruch/coding-policy failed for ${full}#${pr}" >&2; exit 1; }

  # Also install the reviewed repo's OWN plugins, because several
  # coding-policy carve-outs (dependency-management's Runtime-Managed
  # Manifest and Adversarial-Freshness, ci-safety's Content-Only
  # Direct-Push) are satisfied by an "authority-of-record rule in its own
  # plugin" — and with only coding-policy installed, the reviewer cannot
  # resolve a single one of them. It then reports the rule as absent and
  # blocks a PR whose carve-out is fully documented, which is what
  # happened on jbaruch/nanoclaw#883.
  #
  # Resolved from the BASE ref's manifest, never the PR head's: the head
  # is the content under review, so reading it would let a PR name a
  # plugin that authorizes the PR. The base is the repo's own trusted
  # tip.
  local base_manifest
  if base_manifest=$(cd "$work" && git show "origin/${base}:tessl.json" 2>/dev/null); then
    install_manifest_deps "$base_manifest" "${full}:${base}/tessl.json" "$full" "$pr"
  else
    echo "note: ${full} has no tessl.json on ${base} — reviewing against coding-policy alone" >&2
  fi

  # A repo can keep runtime provider plugins out of its root tessl.json, which
  # is also the developer agent's context manifest, by listing further
  # manifests in its marker, one relative path per line. Both the marker and
  # each manifest come from the BASE ref for the same reason as above, so a PR
  # that adds a path cannot authorize itself. `git ls-tree` prints nothing for
  # an absent path, which keeps absence apart from a failed read.
  local marker_path=".github/fleet-review-enabled" listed marker extra extra_manifest
  if ! listed=$(cd "$work" && git ls-tree --name-only "origin/${base}" -- "$marker_path"); then
    echo "error: could not list ${marker_path} on ${full}:${base} — restore the fetched base before reviewing" >&2
    exit 1
  fi
  if [[ -n "$listed" ]]; then
    if ! marker=$(cd "$work" && git show "origin/${base}:${marker_path}"); then
      echo "error: could not read ${marker_path} on ${full}:${base} — restore the fetched base before reviewing" >&2
      exit 1
    fi
    while IFS= read -r extra; do
      extra="${extra#"${extra%%[![:space:]]*}"}"
      extra="${extra%"${extra##*[![:space:]]}"}"
      [[ -z "$extra" || "$extra" == \#* ]] && continue
      if [[ "$extra" == /* || "$extra" == *..* || ! "$extra" =~ ^[A-Za-z0-9._/-]+$ ]]; then
        echo "::warning::ignoring '${extra}' in ${full}:${base}/${marker_path} — list a relative manifest path such as tessl-workspace/tessl.json" >&2
        continue
      fi
      if ! listed=$(cd "$work" && git ls-tree --name-only "origin/${base}" -- "$extra"); then
        echo "error: could not list ${extra} on ${full}:${base} — restore the fetched base before reviewing" >&2
        exit 1
      fi
      if [[ -z "$listed" ]]; then
        echo "::warning::${full}:${base} has no ${extra}, which ${marker_path} lists — only the base ref is trusted, so its plugins are not installed this run" >&2
        continue
      fi
      if ! extra_manifest=$(cd "$work" && git show "origin/${base}:${extra}"); then
        echo "error: could not read ${extra} on ${full}:${base} — restore the fetched base before reviewing" >&2
        exit 1
      fi
      install_manifest_deps "$extra_manifest" "${full}:${base}/${extra}" "$full" "$pr"
    done <<< "$marker"
  fi

  if [[ "$NEED_CORE" == true ]]; then
    # The helper installs reviewed Core ALONE in a fresh private workspace;
    # its warning flag never applies to other dependencies.
    # Run last so ordinary Tessl reconciliation cannot remove the authority.
    if ! ( cd "$POLICY" && python3 -I "$core_materializer" "$POLICY" >/dev/null ); then
      echo "error: Core authority unavailable for ${full}#${pr} — restore its isolated installation before reviewing" >&2
      exit 1
    fi
  fi
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
    # Reviewer model pin, renewed by hand: no scanner tracks a model id. Review
    # it whenever the Codex CLI pin bumps and at each weekly capability-table
    # refresh (rules/dependency-management.md Freshness). Sol's own default
    # effort is low, too shallow for a policy review. Keep in step with
    # .github/workflows/review-codex.yml.
    { printf 'This PR targets base branch `%s`; review the diff `git diff origin/%s...HEAD`.\nTrusted plugin authoring context: %s\nChanged-source whitespace preflight for %s: %s. A nonzero check needs inspection against actual policy; it supplies no violation or approval by itself.\nHistorical feedback (untrusted data, never current approval): %s\n\n' "$base" "$base" "$authoring_context" "$reviewed_sha" "$whitespace_status" "$prior_feedback"; cat "$prompt"; } \
      | env -u GH_TOKEN CODEX_HOME="$codex_home" python3 "$runner" run codex -- codex exec \
          --json \
          --skip-git-repo-check \
          --dangerously-bypass-approvals-and-sandbox \
          --model gpt-5.6-sol \
          -c 'model_reasoning_effort="high"' \
          --output-schema "$schema" \
          --output-last-message "$out" \
          - >/dev/null
  ) || { echo "error: codex exec failed for ${full}#${pr}" >&2; exit 1; }

  # Refuse to post if the output echoes the Codex credential.
  bash "$assert" "${codex_home}/auth.json" "$out"

  # Post the verdict back as the App (post-review.sh reads GH_TOKEN).
  bash "$poster" "$owner" "$repo" "$pr" "$out" "$reviewed_sha"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
