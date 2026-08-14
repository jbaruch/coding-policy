#!/usr/bin/env bash
# Post-publish fail-SAFE gate: decide whether a publish step that exited
# non-zero should still be treated as a landed release.
#
# The problem (rules/ci-safety.md "Credits Never Block Publishing"): a tessl
# org running out of credits makes the publish step exit non-zero AFTER the
# artifact already published —
#   ✔ Published jbaruch/coding-policy@X  ->  ✔ Uploaded evals
#   ->  ##[error]Out of credits  ->  exit 1
# The release LANDED; the non-zero exit is a post-publish billing failure, not
# a publish failure. Left unhandled it reds every fleet publish run whose
# artifact actually shipped. But the fix must stay FAIL-SAFE: a genuine
# non-landing failure (auth, lint, network before publish) MUST still red. A
# fail-OPEN gate that greens a real failure is unacceptable — it would ship
# "released" for a release that never happened.
#
# The decision is the registry itself, not the exit code: did the registry's
# latest version advance past the pre-publish baseline?
#   outcome == success                      -> nothing to reconcile, pass (0)
#   outcome != success AND registry advanced -> post-publish failure after the
#                                               artifact shipped, pass with a
#                                               ::warning:: (0)
#   outcome != success AND NOT advanced      -> genuine failure, nothing landed,
#                                               fail with an ::error:: (1)
#   cannot read the registry (rc 2)          -> cannot confirm -> fail (1)
# "Cannot confirm" fails: the gate never passes a release it could not verify
# landed.
#
# The versions API reflects a publish immediately (registry-version.sh reads
# it, not the lagging plugin-info listing), so NO retry loop is needed — one
# read is authoritative.
#
# Usage: confirm-publish-landed.sh <workspace> <plugin> <baseline> <publish-outcome>
#   <baseline>         the registry's latest version captured BEFORE publish
#                      (empty = tile had never published; a first publish still
#                      counts as an advance past empty)
#   <publish-outcome>  the GitHub Actions steps.<id>.outcome of the publish
#                      step: success | failure | cancelled | skipped
# Out:   a ::warning:: (landed despite non-zero) or ::error:: (did not land /
#        cannot confirm) GitHub workflow annotation; a plain note on the
#        success path.
# Exit:  0 when the publish is confirmed landed (or the step succeeded);
#        1 when it did not land or cannot be confirmed (the gate's fail signal);
#        2 on a usage error (wrong argument count).

set -euo pipefail

_cpl_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <workspace> <plugin> <baseline> <publish-outcome>" >&2
    exit 2
  fi
  local workspace="$1" plugin="$2" baseline="$3" outcome="$4"

  # version-compare.sh (version_gt) tests the registry advance; registry-version
  # .sh reads the current latest. A missing helper is a fail-safe fail: we
  # cannot confirm the artifact landed.
  # shellcheck source=skills/release/version-compare.sh
  if ! source "${_cpl_dir}/version-compare.sh"; then
    echo "::error::confirm-publish-landed: cannot source ${_cpl_dir}/version-compare.sh — the release skill tree is incomplete. Treating as NOT confirmed (fail-safe); re-clone the repo or re-install the plugin." >&2
    exit 1
  fi
  local registry_script="${_cpl_dir}/registry-version.sh"
  if [[ ! -f "$registry_script" || ! -r "$registry_script" ]]; then
    echo "::error::confirm-publish-landed: ${registry_script} is missing or unreadable — the release skill tree is incomplete. Treating as NOT confirmed (fail-safe); re-clone the repo or re-install the plugin." >&2
    exit 1
  fi

  # The publish step's outcome is a HINT, never proof — the authority is the
  # registry (rules/ci-safety.md "Credits Never Block Publishing": whether the
  # artifact published is answered by the registry advance, independent of the
  # run's outcome). So ALWAYS read and compare, including on `success`: a green
  # no-op, a skipped publish, or a publish that did not land must NOT pass the
  # gate just because the step reported success.
  #
  # A subprocess call (not sourced): the gate and its unit suite exercise
  # registry-version.sh through a `tessl` fake on PATH, which a subprocess picks
  # up. registry-version.sh exits non-zero on a tool/parse failure — that is
  # "cannot confirm", which fails the gate.
  local current rc=0
  current=$(bash "$registry_script" "$workspace" "$plugin") || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "::error::confirm-publish-landed: cannot read the registry version for ${workspace}/${plugin} (registry-version.sh exit ${rc}) — cannot confirm the publish landed. Treating as NOT landed (fail-safe); inspect the registry manually with 'tessl api v1/tiles/${workspace}/${plugin}/versions' before retrying." >&2
    exit 1
  fi

  # The registry advanced past the baseline -> the artifact shipped. version_gt
  # handles an empty baseline (first-ever publish) as an advance past 0.0.0; an
  # empty current with an empty baseline compares equal -> not greater -> the
  # not-landed branch below fails the gate.
  if version_gt "$current" "$baseline"; then
    if [[ "$outcome" == "success" ]]; then
      echo "confirm-publish-landed: ${workspace}/${plugin}@${current} landed on the registry (baseline ${baseline:-<none>}), publish step outcome=success — confirmed."
    else
      echo "::warning::confirm-publish-landed: publish step for ${workspace}/${plugin} exited non-zero (outcome=${outcome}) but ${workspace}/${plugin}@${current} landed on the registry (baseline ${baseline:-<none>}). This is a post-publish failure AFTER the artifact shipped (e.g. out-of-credits after '✔ Published'); treating the publish as landed per rules/ci-safety.md Credits Never Block Publishing."
    fi
    exit 0
  fi

  # The registry did NOT advance -> nothing landed, whatever the step reported.
  if [[ "$outcome" == "success" ]]; then
    echo "::error::confirm-publish-landed: publish step for ${workspace}/${plugin} reported outcome=success but the registry did NOT advance — still at baseline ${baseline:-<none>} (current ${current:-<none>}). A no-op, a skipped publish, or a publish that did not land — NOT a confirmed release (rules/ci-safety.md release contract requires the registry to advance). Inspect the publish step." >&2
  else
    echo "::error::confirm-publish-landed: publish step for ${workspace}/${plugin} failed (outcome=${outcome}) and nothing landed — the registry is still at baseline ${baseline:-<none>} (current ${current:-<none>}). This is a genuine publish failure; inspect the run's publish step, fix the cause, and re-run." >&2
  fi
  exit 1
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
