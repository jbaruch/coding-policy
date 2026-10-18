#!/usr/bin/env bash
# Publish an explicitly versioned ACR package from its fetched default branch.
# argv: package root [explicit reviewed source commit]. stdout: acr publish
# --json's result. stderr: diagnostics. An explicit source must equal HEAD and
# remain reachable from the fetched default; normal calls require its exact tip.
# Exit 2 on setup/precondition failure, otherwise acr's status. Creates/pushes
# v<manifest-version> once; never moves a tag. A partial publication is retried
# from that same commit/version and independently verified by the release flow.
set -euo pipefail

main() {
  if (( $# < 1 || $# > 2 )) || [[ ! -d "$1" ]]; then
    echo 'usage: publish-acr.sh <package-root> [explicit-reviewed-source-commit]' >&2
    return 2
  fi
  local root tool validation version head default tag tagged top status rc=0
  for tool in git acr jq; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "publish-acr: install ${tool} before publishing" >&2
      return 2
    fi
  done
  if ! root="$(CDPATH='' cd -- "$1" && pwd -P && printf x)"; then
    echo 'publish-acr: select a readable package with agent-plugin.yaml' >&2
    return 2
  fi
  root="${root%x}"
  root="${root%$'\n'}"
  if [[ ! -f "$root/agent-plugin.yaml" ]] \
      || ! top="$(git -C "$root" rev-parse --show-toplevel && printf x)" \
      || ! status="$(git -C "$root" status --porcelain)"; then
    echo 'publish-acr: restore the readable Git package root before publishing' >&2
    return 2
  fi
  top="${top%x}"
  top="${top%$'\n'}"
  if [[ "$top" != "$root" || -n "$status" ]]; then
    echo 'publish-acr: publish only a clean repository root with committed package files' >&2
    return 2
  fi
  if ! git -C "$root" fetch origin --tags \
      || ! git -C "$root" remote set-head origin --auto >/dev/null \
      || ! default="$(git -C "$root" symbolic-ref --short refs/remotes/origin/HEAD)"; then
    echo 'publish-acr: restore origin access and its default-branch ref before retrying' >&2
    return 2
  fi
  head="$(git -C "$root" rev-parse HEAD)"
  if [[ -n "${2:-}" ]]; then
    if [[ ! "$2" =~ ^[0-9a-f]{40}$ || "$2" != "$head" ]] \
        || ! git -C "$root" merge-base --is-ancestor "$head" "$default"; then
      echo 'publish-acr: explicit source must equal HEAD and belong to the fetched remote default history' >&2
      return 2
    fi
  elif [[ "$(git -C "$root" rev-parse "$default")" != "$head" ]]; then
      echo 'publish-acr: HEAD must equal the fetched remote default branch' >&2
      return 2
  fi
  if ! validation="$(acr validate "$root" --json)" \
      || ! version="$(printf '%s' "$validation" | jq -er '.result | select(.valid == true) | .version | select(type == "string" and length > 0)')"; then
    echo 'publish-acr: repair the manifest and selected package files, then rerun acr validate before publishing' >&2
    return 2
  fi
  tag="v${version}"
  git -C "$root" show-ref --verify --quiet "refs/tags/$tag" || rc=$?
  if (( rc == 0 )); then
    tagged="$(git -C "$root" rev-parse "${tag}^{commit}")"
    if [[ "$tagged" != "$head" ]]; then
      echo "publish-acr: immutable ${tag} already names another commit; reconcile that publication without moving it" >&2
      return 2
    fi
  elif (( rc == 1 )); then
    git -C "$root" tag "$tag" "$head" || return 2
  else
    echo "publish-acr: cannot read ${tag}; repair Git refs before retrying" >&2
    return 2
  fi
  git -C "$root" push origin "refs/tags/$tag" || return 2
  acr publish "$root" --json
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
