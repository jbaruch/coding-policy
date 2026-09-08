#!/usr/bin/env bash
# Answer ONE question, definitively: did <tag> publish a retrievable
# GitHub release on <owner>/<repo>?
#
# This is the non-Tessl channel's published-artifact evidence
# (rules/ci-safety.md "Always Watch CI"). A package published by pushing
# a release tag — an ACR package published by `acr publish` from a
# tag-triggered workflow — has no registry to advance and no moderation
# state to clear, so the Tessl helpers say nothing about whether it
# landed. What answers that is the release itself: it exists at the
# exact tag the run published, it is not a draft, and its assets are
# retrievable.
#
# A green workflow run is not that evidence. `resolve-publish-run.sh`
# plus `gh run watch` establish that the publish job finished; an upload
# that never completed, a draft release left unpublished, or a release
# created at a different tag all survive a `success` conclusion. Both
# checks are required, and neither substitutes for the other.
#
# Asset retrievability is read from GitHub's own `state` field: an asset
# mid-upload or failed reports something other than `uploaded`, and the
# download it fronts 404s. A release with zero assets is reported as a
# failed conjunct, never as a vacuous pass.
#
# Discriminating absent from indeterminate, the same way
# registry-has-version.sh does: `gh api` exits non-zero on a 404 AND on
# an auth or network failure, so a non-zero exit alone is not "absent".
# The `HTTP 404` in gh's diagnostic is the discriminator. Anything else
# is INDETERMINATE and exits 2 — a caller that cannot tell must never
# claim the artifact landed (fail closed).
#
# jq is deliberately NOT used: the field extraction runs inside gh's own
# `--jq`, so this script carries no system-jq dependency (same reasoning
# as resolve-publish-run.sh).
#
# Usage: verify-github-release.sh <owner> <repo> <tag>
# Out:   one JSON object on stdout —
#          rc 0: {"ok":true,"tag":"...","assets":N,"url":"..."}
#          rc 1: {"ok":false,"reason":"...","tag":"...","assets":N}
#        Diagnostics go to stderr. On exit 2 stdout is empty.
# Exit:  0 the release is published and every asset is retrievable;
#        1 a definitive no (release absent, draft, no assets, or an
#          asset not in `uploaded` state);
#        2 indeterminate (gh absent or unreachable, unparseable body) or
#          a usage error

set -euo pipefail

# Script-global, NOT a main() local: the EXIT trap fires after main()
# returns, when a main-local would be out of scope and `set -u` would
# turn cleanup into an unbound-variable failure.
VGR_ERR_FILE=""

# EXIT-trap cleanup. `return 0` is load-bearing: an EXIT trap's final
# command status becomes the script's exit status, so a failing `rm`
# would rewrite a deliberate `exit 1` (rules/error-handling.md Shell
# Error Handling).
cleanup_vgr_err_file() {
  if [[ -n "${VGR_ERR_FILE:-}" ]]; then
    if ! rm -f "$VGR_ERR_FILE"; then
      echo "verify-github-release.sh: warning: could not remove temp file ${VGR_ERR_FILE} — remove it by hand" >&2
    fi
    VGR_ERR_FILE=""
  fi
  return 0
}
trap cleanup_vgr_err_file EXIT

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <tag>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" tag="$3"
  if [[ -z "$owner" || -z "$repo" || -z "$tag" ]]; then
    echo "error: owner, repo and tag must all be non-empty — got '${owner}' '${repo}' '${tag}'" >&2
    exit 2
  fi
  if ! command -v gh >/dev/null 2>&1; then
    echo "error: gh not found on PATH — install the GitHub CLI (https://cli.github.com) and run 'gh auth login'" >&2
    exit 2
  fi

  VGR_ERR_FILE=$(mktemp -t verify-github-release.XXXXXX)

  # One TSV row: tagName, draft flag, uploaded-asset count, total asset
  # count, release URL. `@tsv` keeps the parse in read's hands, so no
  # system jq is needed to take the envelope apart.
  local row rc=0
  row=$(gh api "repos/${owner}/${repo}/releases/tags/${tag}" \
    --jq '[.tag_name, (.draft|tostring), ([.assets[]|select(.state == "uploaded")]|length|tostring), (.assets|length|tostring), .html_url] | @tsv' \
    2>"$VGR_ERR_FILE") || rc=$?

  if (( rc != 0 )); then
    local gh_err
    gh_err=$(cat "$VGR_ERR_FILE")
    if [[ "$gh_err" == *"HTTP 404"* ]]; then
      printf '{"ok":false,"reason":"no release exists at tag %s in %s/%s","tag":"%s","assets":0}\n' \
        "$tag" "$owner" "$repo" "$tag"
      exit 1
    fi
    echo "error: could not read repos/${owner}/${repo}/releases/tags/${tag} — ${gh_err}" >&2
    echo "check 'gh auth status', then retry 'gh api repos/${owner}/${repo}/releases/tags/${tag}'" >&2
    exit 2
  fi

  local actual_tag draft uploaded total url
  IFS=$'\t' read -r actual_tag draft uploaded total url <<<"$row"

  if [[ -z "$actual_tag" || -z "$draft" || -z "$uploaded" || -z "$total" ]]; then
    echo "error: unparseable release payload for tag ${tag} in ${owner}/${repo}, got: '${row}' — inspect 'gh api repos/${owner}/${repo}/releases/tags/${tag}' to diagnose" >&2
    exit 2
  fi

  # A caller that asked for a version must be told about the version it
  # got, never about a near-miss the API resolved on its behalf.
  if [[ "$actual_tag" != "$tag" ]]; then
    printf '{"ok":false,"reason":"release at %s reports tag %s","tag":"%s","assets":%s}\n' \
      "$tag" "$actual_tag" "$tag" "$total"
    exit 1
  fi
  if [[ "$draft" == "true" ]]; then
    printf '{"ok":false,"reason":"release %s is still a draft and is not publicly retrievable","tag":"%s","assets":%s}\n' \
      "$tag" "$tag" "$total"
    exit 1
  fi
  if [[ "$total" == "0" ]]; then
    printf '{"ok":false,"reason":"release %s carries no assets","tag":"%s","assets":0}\n' "$tag" "$tag"
    exit 1
  fi
  if [[ "$uploaded" != "$total" ]]; then
    printf '{"ok":false,"reason":"%s of %s assets on release %s are in the uploaded state","tag":"%s","assets":%s}\n' \
      "$uploaded" "$total" "$tag" "$tag" "$total"
    exit 1
  fi

  printf '{"ok":true,"tag":"%s","assets":%s,"url":"%s"}\n' "$tag" "$total" "$url"
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
