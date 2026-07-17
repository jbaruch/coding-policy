#!/usr/bin/env bash
# Wait for a published plugin version's moderation to clear, polling the
# registry with exponential backoff. Moderation is a post-publish install
# gate: a freshly published version can be blocked from `tessl install`
# until its moderation state reaches "pass". This is the release
# contract's third conjunct (rules/ci-safety.md) — a green publish run
# plus a registry advance does NOT confirm a release on its own.
#
# Signal (machine-readable JSON, not the `tessl plugin info` human text):
#   tessl api v1/tiles/<workspace>/<tile>/versions/<version>
#     .data.attributes.moderationStatus   ("pass" => cleared)
#     .data.attributes.moderationPassed    (bool; true => cleared)
#     .data.attributes.moderationError     (non-null => blocked)
#
# Backoff: the per-attempt delay starts at BASE_DELAY_SEC and doubles each
# attempt, capped at MAX_DELAY_SEC; the loop stops once cumulative sleep
# would exceed BUDGET_SEC. Constants below; override via the matching
# env vars (used by the test harness to keep runs fast).
#
# Fail-loud-at-budget: a still-pending OR blocked state when the budget is
# exhausted exits 1 — an unconfirmed release is never reported as success.
#
# Usage: verify-moderation-cleared.sh <workspace> <plugin> <version>
# Out:   JSON contract differs by exit code (per rules/script-delegation.md
#        "JSON-producing"):
#          - rc 0/1 (moderation finding): one JSON object on stdout
#              {"ok": bool, "reason": "<human text>",
#               "moderation_status": "<status-or-empty>",
#               "version": "<version>", "attempts": N, "elapsed_seconds": N}
#          - rc 2 (tool-state error): stderr-only diagnostic, stdout empty
#            (or, for the missing-jq guard, a minimal JSON envelope with the
#            same fields and "ok": false). Wrappers MUST parse stdout only
#            when exit code is 0 or 1.
# Exit:  0 moderation cleared; 1 blocked or still-pending at budget
#        exhaustion (fail loud); 2 argument-validation or external-tool
#        failure (tessl unreachable, jq missing, bad env var).

set -euo pipefail

# --- Tunable constants (script is the source of truth; rules/ci-safety.md
# names the contract and points here rather than restating these). ---
BASE_DELAY_SEC="${VERIFY_MODERATION_BASE_DELAY_SEC:-5}"
MAX_DELAY_SEC="${VERIFY_MODERATION_MAX_DELAY_SEC:-120}"
BUDGET_SEC="${VERIFY_MODERATION_BUDGET_SEC:-600}"

# Terminal "blocked" moderation states — fast-fail without waiting out the
# budget. Anything not in here and not "pass" is treated as still-pending
# and polled until the budget is exhausted.
BLOCK_STATES="fail failed flagged blocked rejected reject denied"

if ! command -v jq >/dev/null 2>&1; then
  printf '{"ok":false,"reason":"jq is not installed; install with '"'"'brew install jq'"'"' (macOS) or '"'"'apt install jq'"'"' (Debian/Ubuntu) and re-run","moderation_status":"","version":"","attempts":0,"elapsed_seconds":0}\n'
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

# EXIT-trap cleanup. `return 0` is load-bearing: the trap's final command
# status becomes the script's exit status, so a failing `rm` would rewrite
# this script's verdict (rules/error-handling.md Shell Error Handling).
#
# `if ! rm` rather than a bare `rm`: under `set -e` a failing rm aborts the
# handler before `return 0` runs — reintroducing the exact rewrite the
# handler exists to prevent. An `if` condition suspends `set -e`, so the
# failure is reported instead of escaping.
cleanup_err_file() {
  if [[ -n "${err_file:-}" ]]; then
    if ! rm -f "$err_file"; then
      echo "verify-moderation-cleared.sh: warning: could not remove temp file ${err_file} — remove it by hand" >&2
    fi
  fi
  return 0
}

emit_and_exit() {
  local ok="$1" reason="$2" status="$3" version="$4" attempts="$5" elapsed="$6" rc="$7"
  printf '{"ok":%s,"reason":%s,"moderation_status":%s,"version":%s,"attempts":%s,"elapsed_seconds":%s}\n' \
    "$ok" \
    "$(printf '%s' "$reason"  | jq -Rs .)" \
    "$(printf '%s' "$status"  | jq -Rs .)" \
    "$(printf '%s' "$version" | jq -Rs .)" \
    "$attempts" \
    "$elapsed"
  exit "$rc"
}

validate_positive_int() {
  local name="$1" value="$2"
  if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: ${name} must be a positive integer, got: '${value}'" >&2
    exit 2
  fi
}

is_block_state() {
  local s="$1" b
  for b in $BLOCK_STATES; do
    [[ "$s" == "$b" ]] && return 0
  done
  return 1
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <workspace> <plugin> <version>" >&2
    exit 2
  fi
  local workspace="$1" tile="$2" version="$3"
  if [[ -z "$workspace" || -z "$tile" || -z "$version" ]]; then
    echo "error: <workspace>, <plugin>, and <version> are all required and must be non-empty — capture the published version from the registry advance confirmed by verify-publish-landed.sh" >&2
    exit 2
  fi

  validate_positive_int "VERIFY_MODERATION_BASE_DELAY_SEC" "$BASE_DELAY_SEC"
  validate_positive_int "VERIFY_MODERATION_MAX_DELAY_SEC" "$MAX_DELAY_SEC"
  validate_positive_int "VERIFY_MODERATION_BUDGET_SEC" "$BUDGET_SEC"
  if (( BASE_DELAY_SEC > MAX_DELAY_SEC )); then
    echo "error: VERIFY_MODERATION_BASE_DELAY_SEC (${BASE_DELAY_SEC}) must not exceed VERIFY_MODERATION_MAX_DELAY_SEC (${MAX_DELAY_SEC})" >&2
    exit 2
  fi
  if (( BASE_DELAY_SEC > BUDGET_SEC )); then
    echo "error: VERIFY_MODERATION_BASE_DELAY_SEC (${BASE_DELAY_SEC}) must not exceed VERIFY_MODERATION_BUDGET_SEC (${BUDGET_SEC})" >&2
    exit 2
  fi

  local endpoint="v1/tiles/${workspace}/${tile}/versions/${version}"
  local delay="$BASE_DELAY_SEC" elapsed=0 attempts=0
  local err_file; err_file=$(mktemp) || { echo "error: mktemp failed — cannot run verify-moderation-cleared.sh without writable TMPDIR" >&2; exit 2; }
  # Named handler ending `return 0` — the EXIT trap's final command status
  # becomes the script's exit status, so a failing `rm` would rewrite the
  # moderation verdict into a bare 1 (rules/error-handling.md).
  trap cleanup_err_file EXIT

  while :; do
    attempts=$(( attempts + 1 ))

    # Separate stdout (the JSON body) from stderr so a tessl warning can't
    # poison the parsed payload — same capture discipline as
    # verify-publish-landed.sh.
    local body
    body=$(tessl api "$endpoint" 2>"$err_file") \
      || { local err; err=$(cat "$err_file"); echo "error: 'tessl api ${endpoint}' failed: ${err} — verify (1) tessl CLI is installed and on PATH ('command -v tessl'), (2) the workspace/plugin/version slug is correct, (3) you have network access to the registry, then re-run; the version was already confirmed on the registry by verify-publish-landed.sh, so a persistent failure here is a tool/auth/network problem, not a missing version" >&2; exit 2; }

    # Validate the payload ONCE, explicitly, before extracting fields.
    # Each extraction previously carried `2>/dev/null || true`, collapsing
    # two different states into the same empty string: a field legitimately
    # absent (what `// empty` is for), and a body that is not JSON at all
    # (a proxy error page, an HTML 502). Both then reached the same
    # "response shape may have changed — update this script's jq paths"
    # diagnostic below. That exit code was right and the message was wrong:
    # it sends the operator to rewrite a parse that works, against a
    # registry that is returning 502s (rules/error-handling.md Actionable
    # Messages).
    # `jq empty`, not `jq -e .`: `-e` sets the exit code from the last
    # OUTPUT's truthiness, so a body that is valid JSON but evaluates to
    # `false` or `null` would exit 1 and be misreported "not valid JSON".
    # `jq empty` parses and produces no output — exit reflects parse
    # validity alone.
    if ! printf '%s' "$body" | jq empty >/dev/null 2>&1; then
      echo "error: 'tessl api ${endpoint}' returned a body that is not valid JSON — the registry may be returning an error page or the endpoint shape changed; inspect it directly with 'tessl api ${endpoint}' before retrying (body was: ${body})" >&2
      exit 2
    fi

    # `.attributes?` suppresses the index error jq raises when `.data` is
    # valid JSON of the wrong TYPE (a string/array, e.g. a proxy error page
    # that parsed) — without it, the call hard-fails under `set -e` and the
    # script exits on jq's raw error, skipping the shape-changed diagnostic
    # below. With `?`, a wrong shape yields empty for all three and falls
    # through to that diagnostic. Three separate calls, not a `@tsv` read —
    # tab is IFS-whitespace, so a leading empty field would shift the values.
    local status passed mod_error
    status=$(printf '%s' "$body" | jq -r '.data.attributes?.moderationStatus // empty')
    passed=$(printf '%s' "$body" | jq -r '.data.attributes?.moderationPassed // empty')
    mod_error=$(printf '%s' "$body" | jq -r '.data.attributes?.moderationError // empty')

    # A moderationError alone is a valid (blocked) response, so it counts
    # as a parsed field — without it in the guard, a block-by-error result
    # with no status/passed would wrongly exit 2 instead of the rc 1
    # blocked finding below.
    if [[ -z "$status" && -z "$passed" && -z "$mod_error" ]]; then
      echo "error: could not parse moderation fields from 'tessl api ${endpoint}' (body was: ${body}) — the registry response shape may have changed; inspect it directly and update verify-moderation-cleared.sh's jq paths" >&2
      exit 2
    fi

    # Cleared: explicit pass status or the boolean flag.
    if [[ "$status" == "pass" || "$passed" == "true" ]]; then
      emit_and_exit "true" \
        "moderation cleared for ${workspace}/${tile}@${version} (status=${status:-pass}) after ${attempts} check(s), ${elapsed}s" \
        "$status" "$version" "$attempts" "$elapsed" 0
    fi

    # Blocked: a non-null moderation error or a terminal block status —
    # fail loud immediately rather than waiting out the budget.
    if [[ -n "$mod_error" ]] || is_block_state "$status"; then
      emit_and_exit "false" \
        "moderation BLOCKED for ${workspace}/${tile}@${version} (status=${status:-unknown}${mod_error:+, error=${mod_error}}) — the version is published but install-gated; resolve the moderation finding (see https://tessl.io/registry/${workspace}/${tile}/security) before treating the release as confirmed" \
        "$status" "$version" "$attempts" "$elapsed" 1
    fi

    # Still pending. Stop if the next sleep would exceed the budget.
    if (( elapsed + delay > BUDGET_SEC )); then
      emit_and_exit "false" \
        "moderation still pending for ${workspace}/${tile}@${version} (status=${status:-pending}) after ${attempts} check(s) and ${elapsed}s — budget ${BUDGET_SEC}s exhausted; the release is NOT confirmed. Re-run verify-moderation-cleared.sh ${workspace} ${tile} ${version} to keep waiting, or inspect the registry if it stays pending" \
        "$status" "$version" "$attempts" "$elapsed" 1
    fi

    sleep "$delay"
    elapsed=$(( elapsed + delay ))
    delay=$(( delay * 2 ))
    (( delay > MAX_DELAY_SEC )) && delay="$MAX_DELAY_SEC"
  done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
