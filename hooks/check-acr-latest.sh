#!/usr/bin/env bash
# Update ACR dependencies at session start in a project that uses ACR.
#
# The ACR counterpart of hooks/check-tessl-latest.sh. A project with an
# `agents.yaml` gets `acr freshness run --policy install`, which reconciles
# every dependency declared as `latest` and realizes the changed files; pinned
# tags and commits never move. The policy is passed explicitly, so a project
# whose `agents.yaml` still says `outdated` (ACR's default) is updated rather
# than told it is behind. ACR throttles remote checks per project and policy to
# one per 24 hours, so this and ACR's own session-start hook at `install` share
# one check.
#
# Run from hooks/session-start.sh, which merges its status with the others.
#
# Contract:
#   stdin : not read.
#   stdout: one JSON object {"additionalContext": "<status>"} whose text begins
#           with "Session-start status — acr: " when ACR reported something or
#           failed, or when `acr` is missing from a project that needs it.
#           Nothing for a project without `agents.yaml`, a throttled or
#           no-change run, and a Herdr worker session.
#   stderr: diagnostics only.
#   exit  : always 0.
#   env   : ACR_BIN names the acr executable (default `acr`), as ACR's own hook does.
set -euo pipefail

warn() { printf 'check-acr-latest: %s\n' "$1" >&2; }

emit() { # <status text>
  if command -v python3 >/dev/null; then
    python3 -c 'import json, sys; print(json.dumps({"additionalContext": sys.argv[1]}))' "$1"
  elif command -v jq >/dev/null; then
    jq -n --arg c "$1" '{additionalContext: $c}'
  else
    warn "neither python3 nor jq is on PATH — cannot report the ACR status"
  fi
}

# A Herdr worker in a linked worktree must not realize files into its checkout
# on the hook's say-so (rules/agent-team-operation.md Writers and Checkouts).
is_herdr_worker() {
  [[ -n "${HERDR_ENV:-}" ]] || return 1
  local git_dir common_dir
  git_dir="$(git rev-parse --absolute-git-dir)" || return 1
  common_dir="$(git rev-parse --path-format=absolute --git-common-dir)" || return 1
  [[ "$git_dir" != "$common_dir" ]]
}

main() {
  local root acr out rc=0
  # The project root is the git toplevel, or the working directory outside git.
  if ! root="$(git rev-parse --show-toplevel 2>&1)"; then
    root="$PWD"
  fi
  [[ -f "${root}/agents.yaml" ]] || return 0
  if is_herdr_worker; then
    return 0
  fi

  acr="${ACR_BIN:-acr}"
  if ! command -v "$acr" >/dev/null; then
    emit "Session-start status — acr: this project declares ACR dependencies (agents.yaml) but \`${acr}\` is not on PATH, so they were not updated; install it with \`brew install jbaruch/agentic-context-registry/acr\` or set ACR_BIN."
    return 0
  fi

  out="$("$acr" freshness run --project "$root" --policy install 2>&1)" || rc=$?
  if (( rc != 0 )); then
    emit "Session-start status — acr: updating ACR dependencies failed (exit ${rc}):"$'\n'"${out}"$'\n'"Run \`${acr} freshness run --project ${root} --policy install\` to diagnose it."
    return 0
  fi
  [[ -n "${out//[[:space:]]/}" ]] || return 0
  emit "Session-start status — acr:"$'\n'"${out}"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no ACR status this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
  fi
  exit 0
fi
