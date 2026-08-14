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
# non-landing failure (auth, lint, network before publish) MUST still red, and
# so must a DIFFERENT post-publish failure that also lands the artifact — a
# genuine eval failure, a bump-push failure. A registry advance alone does not
# prove the non-zero exit was the tolerated out-of-credits case.
#
# So the tolerance is gated on TWO facts, not one:
#   1. the registry advanced past the pre-publish baseline (the artifact
#      shipped), AND
#   2. the org is VERIFIABLY out of credits (org-credit-blocked.sh reads
#      `tessl org usage --org <workspace> --json` -> .credits.blocked == true).
# Only when both hold is a non-zero exit tolerated. If the credit state cannot
# be read, the gate cannot verify this is the credit case and stays RED.
#
# The org-credit check is performed ONLY on the (non-success + advanced) path —
# never queried otherwise.
#
# Fail-safe decision table (implemented exactly):
#   outcome  | advanced | credit-blocked | gate
#   ---------+----------+----------------+---------------------------------------
#   success  | yes      | (not checked)  | PASS — confirmed
#   success  | no       | (not checked)  | FAIL — green no-op / did not land
#   non-succ | yes      | YES            | PASS — credit outage, artifact landed
#   non-succ | yes      | NO             | FAIL — non-credit post-publish failure
#   non-succ | no       | (not checked)  | FAIL — nothing landed
#   read err | —        | —              | FAIL — cannot read registry, cannot confirm
#   non-succ | yes      | UNKNOWN        | FAIL — cannot verify it is the credit case
#
# The versions API reflects a publish immediately (registry-version.sh reads
# it, not the lagging plugin-info listing), so NO retry loop is needed — one
# read is authoritative.
#
# Output (rules/script-delegation.md — deterministic scripts emit JSON, not
# annotation prose): ONE JSON object on stdout —
#   {"gate":"pass"|"fail","landed":true|false,"current":"x.y.z"|null,
#    "baseline":"x.y.z"|null,"credit_blocked":true|false|null,
#    "reason":"<one-line human summary>"}
# credit_blocked is null when the credit state was not checked or not readable.
# NO ::warning::/::error:: is emitted here — the publish-landed-gate composite
# action translates this JSON into the workflow annotations and the job verdict.
# The EXIT CODE still reflects the gate so a direct run is usable: 0 pass, 1
# fail, 2 usage error.
#
# Usage: confirm-publish-landed.sh <workspace> <plugin> <baseline> <publish-outcome>
#   <baseline>         the registry's latest version captured BEFORE publish
#                      (empty = tile had never published; a first publish still
#                      counts as an advance past empty)
#   <publish-outcome>  the GitHub Actions steps.<id>.outcome of the publish
#                      step: success | failure | cancelled | skipped
# Exit:  0 when the publish is confirmed landed (or the step succeeded);
#        1 when it did not land or cannot be confirmed (the gate's fail signal);
#        2 on a usage error (wrong argument count).

set -euo pipefail

_cpl_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# JSON string escaper for the human `reason` and the string fields. Escapes the
# JSON-mandatory backslash and double-quote plus the common control chars, so a
# reason built from tool output can never produce invalid JSON.
json_str() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\r'/\\r}"
  printf '"%s"' "$s"
}

# Emit the gate result as one JSON object and exit with the gate's code.
#   gate:           "pass" | "fail"
#   landed:         true | false        (raw JSON token — registry advanced)
#   current:        version string or "" (-> JSON null)
#   baseline:       version string or "" (-> JSON null)
#   credit_blocked: true | false | null (raw JSON token; null = not checked)
#   reason:         human text
#   rc:             process exit code (0 pass, 1 fail)
emit_and_exit() {
  local gate="$1" landed="$2" current="$3" baseline="$4" credit_blocked="$5" reason="$6" rc="$7"
  local current_json baseline_json
  if [[ -z "$current" ]]; then current_json="null"; else current_json="$(json_str "$current")"; fi
  if [[ -z "$baseline" ]]; then baseline_json="null"; else baseline_json="$(json_str "$baseline")"; fi
  printf '{"gate":%s,"landed":%s,"current":%s,"baseline":%s,"credit_blocked":%s,"reason":%s}\n' \
    "$(json_str "$gate")" "$landed" "$current_json" "$baseline_json" "$credit_blocked" "$(json_str "$reason")"
  exit "$rc"
}

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <workspace> <plugin> <baseline> <publish-outcome>" >&2
    exit 2
  fi
  local workspace="$1" plugin="$2" baseline="$3" outcome="$4"

  # version-compare.sh (version_gt) tests the registry advance; registry-version
  # .sh reads the current latest; org-credit-blocked.sh reads the credit state.
  # A missing helper is a fail-safe FAIL: we cannot confirm the artifact landed.
  # shellcheck source=skills/release/version-compare.sh
  if ! source "${_cpl_dir}/version-compare.sh"; then
    emit_and_exit "fail" false "" "$baseline" null \
      "confirm-publish-landed: cannot source ${_cpl_dir}/version-compare.sh — the release skill tree is incomplete. Treating as NOT confirmed (fail-safe); re-clone the repo or re-install the plugin." 1
  fi
  local registry_script="${_cpl_dir}/registry-version.sh"
  if [[ ! -f "$registry_script" || ! -r "$registry_script" ]]; then
    emit_and_exit "fail" false "" "$baseline" null \
      "confirm-publish-landed: ${registry_script} is missing or unreadable — the release skill tree is incomplete. Treating as NOT confirmed (fail-safe); re-clone the repo or re-install the plugin." 1
  fi

  # The publish step's outcome is a HINT, never proof — the authority is the
  # registry (rules/ci-safety.md "Credits Never Block Publishing"). So ALWAYS
  # read and compare, including on `success`: a green no-op, a skipped publish,
  # or a publish that did not land must NOT pass just because the step reported
  # success.
  #
  # A subprocess call (not sourced): the gate and its unit suite exercise
  # registry-version.sh through a `tessl` fake on PATH, which a subprocess picks
  # up. registry-version.sh now emits {"version":...} JSON and exits non-zero on
  # a tool/parse failure — that is "cannot confirm", which fails the gate.
  local rv_json rc=0
  rv_json=$(bash "$registry_script" "$workspace" "$plugin") || rc=$?
  if [[ $rc -ne 0 ]]; then
    emit_and_exit "fail" false "" "$baseline" null \
      "confirm-publish-landed: cannot read the registry version for ${workspace}/${plugin} (registry-version.sh exit ${rc}) — cannot confirm the publish landed. Treating as NOT landed (fail-safe); inspect the registry manually with 'tessl api v1/tiles/${workspace}/${plugin}/versions' before retrying." 1
  fi
  local current
  if ! current=$(printf '%s' "$rv_json" | jq -r '.version // empty'); then
    emit_and_exit "fail" false "" "$baseline" null \
      "confirm-publish-landed: registry-version.sh returned an unparseable payload ('${rv_json}') for ${workspace}/${plugin} — cannot confirm the publish landed. Treating as NOT landed (fail-safe)." 1
  fi

  # The registry advanced past the baseline -> the artifact shipped. version_gt
  # handles an empty baseline (first-ever publish) as an advance past 0.0.0; an
  # empty current with an empty baseline compares equal -> not greater -> the
  # not-landed branch below fails the gate.
  if version_gt "$current" "$baseline"; then
    if [[ "$outcome" == "success" ]]; then
      emit_and_exit "pass" true "$current" "$baseline" null \
        "confirm-publish-landed: ${workspace}/${plugin}@${current} landed on the registry (baseline ${baseline:-<none>}), publish step outcome=success — confirmed." 0
    fi

    # Non-success but the registry advanced. Tolerated ONLY when the org is
    # verifiably out of credits — otherwise this is a non-credit post-publish
    # failure that also landed the artifact (a genuine eval failure, a bump-push
    # failure) and MUST stay red. Query the credit state on THIS path only.
    local credit_script="${_cpl_dir}/org-credit-blocked.sh"
    if [[ ! -f "$credit_script" || ! -r "$credit_script" ]]; then
      emit_and_exit "fail" true "$current" "$baseline" null \
        "confirm-publish-landed: ${credit_script} is missing or unreadable — cannot verify the org credit state, so cannot confirm the non-zero publish exit (outcome=${outcome}) was the tolerated out-of-credits case even though ${workspace}/${plugin}@${current} landed. Preserving RED (fail-safe); re-clone the repo or re-install the plugin." 1
    fi
    local credit_json crc=0
    credit_json=$(bash "$credit_script" "$workspace") || crc=$?
    if [[ $crc -ne 0 ]]; then
      emit_and_exit "fail" true "$current" "$baseline" null \
        "confirm-publish-landed: publish step for ${workspace}/${plugin} exited non-zero (outcome=${outcome}) and ${workspace}/${plugin}@${current} landed, but the org credit state could not be read (org-credit-blocked.sh exit ${crc}) — cannot verify this is a tolerated out-of-credits exit. Preserving RED (fail-safe); inspect 'tessl org usage --org ${workspace} --json'." 1
    fi
    local blocked
    if ! blocked=$(printf '%s' "$credit_json" | jq -r '.blocked'); then
      emit_and_exit "fail" true "$current" "$baseline" null \
        "confirm-publish-landed: org-credit-blocked.sh returned an unparseable payload ('${credit_json}') for org ${workspace} — cannot verify the credit state. Preserving RED (fail-safe)." 1
    fi
    if [[ "$blocked" == "true" ]]; then
      emit_and_exit "pass" true "$current" "$baseline" true \
        "confirm-publish-landed: publish step for ${workspace}/${plugin} exited non-zero (outcome=${outcome}) but ${workspace}/${plugin}@${current} landed AND the org is out of credits (credits.blocked=true) — a post-publish out-of-credits exit AFTER the artifact shipped; treating as landed per rules/ci-safety.md Credits Never Block Publishing." 0
    fi
    emit_and_exit "fail" true "$current" "$baseline" false \
      "confirm-publish-landed: publish step for ${workspace}/${plugin} exited non-zero (outcome=${outcome}) and ${workspace}/${plugin}@${current} landed, but the org is NOT out of credits (credits.blocked=false) — this is a non-credit post-publish failure (e.g. a genuine eval failure or a bump-push failure), NOT the tolerated out-of-credits case. Preserving RED; inspect the run's failing step." 1
  fi

  # The registry did NOT advance -> nothing landed, whatever the step reported.
  # The org-credit check is NOT reached here — nothing shipped to tolerate.
  if [[ "$outcome" == "success" ]]; then
    emit_and_exit "fail" false "$current" "$baseline" null \
      "confirm-publish-landed: publish step for ${workspace}/${plugin} reported outcome=success but the registry did NOT advance — still at baseline ${baseline:-<none>} (current ${current:-<none>}). A no-op, a skipped publish, or a publish that did not land — NOT a confirmed release (rules/ci-safety.md release contract requires the registry to advance). Inspect the publish step." 1
  fi
  emit_and_exit "fail" false "$current" "$baseline" null \
    "confirm-publish-landed: publish step for ${workspace}/${plugin} failed (outcome=${outcome}) and nothing landed — the registry is still at baseline ${baseline:-<none>} (current ${current:-<none>}). A genuine publish failure; inspect the run's publish step, fix the cause, and re-run." 1
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
