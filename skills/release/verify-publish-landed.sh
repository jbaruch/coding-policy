#!/usr/bin/env bash
# Verify a plugin publish actually landed on the registry by checking BOTH
# (a) the resolved publish run's conclusion and (b) the registry's latest
# published version against a pre-merge baseline. Both signals together
# close the queued/in-flight publish race (issue #80): an interleaved
# earlier publish can advance the registry between PRE capture and our
# post-merge check, producing a false-positive against OUR failed run if
# we used registry-advance alone.
#
# The current version is read from the authoritative versions API
# (`tessl api v1/tiles/<ws>/<tile>/versions`), NOT the `tessl plugin info`
# search listing. The listing is eventually-consistent and lags the version
# API by minutes, so reading it false-negatived a publish that had actually
# landed — and, worse, the failure text confidently blamed a nonexistent
# no-op publish step and sent the operator hunting through green run logs
# (#181). The version API reflects a publish immediately. verify-moderation-
# cleared.sh reads the same API family for the same reason.
#
# Conjunction (both required):
#   1. The resolved run's conclusion is `success`. A failed conclusion
#      means THIS publish did not run to completion; any subsequent
#      registry advance came from a different, interleaved run and must
#      NOT be attributed to ours.
#   2. The registry's `Latest Version` is strictly greater than the
#      pre-merge baseline. A non-advance after a `success` conclusion
#      means the workflow exited cleanly without publishing (conditional
#      skip, no-op publish step) and must NOT be reported as published.
#
# Trade-off: a workflow whose conclusion=failure was triggered by a
# post-publish step (e.g., a notification step downstream of the publish
# step) will produce a loud false-negative under this contract. The
# previous "registry-advanced is authoritative" framing accepted the
# silent race instead. The loud false-negative is safer (operator
# checks the registry, sees the publish landed) and the root fix is at
# the workflow design layer — keep publish as the last step.
#
# Usage: verify-publish-landed.sh <workspace> <plugin> <pre-baseline> <run-id>
# Out:   JSON contract differs by exit code (per rules/script-delegation.md
#        "JSON-producing"):
#          - rc 0/1 (publish-landed finding): one JSON object on stdout
#              {"ok": bool, "reason": "<human text>",
#               "run_conclusion": "<gh-run-conclusion>",
#               "pre": "<pre-baseline>", "current": "<current-latest-version>"}
#          - rc 2 (tool-state error): stderr-only diagnostic, stdout is
#            empty (or, for the missing-jq guard, a minimal JSON envelope
#            with the same five fields and "ok": false). Wrappers MUST
#            parse stdout only when exit code is 0 or 1.
# Exit:  0 if both conjuncts hold; 1 if either conjunct fails (publish did
#        not land); 2 on argument-validation or external-tool failures
#        (run still in flight, jq missing, gh/tessl unreachable)

set -euo pipefail

# jq is required for the JSON emitter. Without an early gate, a missing
# jq would terminate the script under `set -e` at the first `jq -Rs .`
# call with no JSON on stdout, breaking wrappers that parse the
# documented output. Hand-roll the missing-jq diagnostic so the failure
# satisfies the JSON contract even when jq itself is absent (same
# pattern as skills/install-reviewer/preflight.sh). Also emit to stderr
# per `rules/script-delegation.md` "Self-error-handling: exit non-zero
# on failure, write a diagnostic message to stderr" — log-watchers and
# stderr-only wrappers need the failure as well.
if ! command -v jq >/dev/null 2>&1; then
  printf '{"ok":false,"reason":"jq is not installed; install with '"'"'brew install jq'"'"' (macOS) or '"'"'apt install jq'"'"' (Debian/Ubuntu) and re-run","run_conclusion":"","pre":"","current":""}\n'
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

emit_and_exit() {
  local ok="$1" reason="$2" conclusion="$3" pre="$4" current="$5" rc="$6"
  printf '{"ok":%s,"reason":%s,"run_conclusion":%s,"pre":%s,"current":%s}\n' \
    "$ok" \
    "$(printf '%s' "$reason"     | jq -Rs .)" \
    "$(printf '%s' "$conclusion" | jq -Rs .)" \
    "$(printf '%s' "$pre"        | jq -Rs .)" \
    "$(printf '%s' "$current"    | jq -Rs .)"
  exit "$rc"
}

# `err_file` is script-global, never `local` in main: the EXIT trap fires
# after main returns, when a main-local would be out of scope and this
# handler would silently skip cleanup (verified: the normal-return path
# leaks the tempfile every run; only the exit-from-inside-main paths clean up).
err_file=""

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
      echo "verify-publish-landed.sh: warning: could not remove temp file ${err_file} — remove it by hand" >&2
    fi
  fi
  return 0
}

# Returns 0 iff $1 > $2 under semver ordering. Parses major.minor.patch
# as integers in pure bash so the comparison stays portable across GNU
# coreutils (Linux CI) and BSD userland (macOS) — `sort -V` is a GNU
# extension and isn't guaranteed on BSD sort. Equality returns non-zero
# so the caller can distinguish strict advance from no-op. Missing parts
# default to 0 via parameter expansion.
version_gt() {
  [[ "$1" != "$2" ]] || return 1
  local a1 a2 a3 b1 b2 b3
  IFS='.' read -r a1 a2 a3 <<< "$1"
  IFS='.' read -r b1 b2 b3 <<< "$2"
  a1=${a1:-0}; a2=${a2:-0}; a3=${a3:-0}
  b1=${b1:-0}; b2=${b2:-0}; b3=${b3:-0}
  (( a1 > b1 )) && return 0
  (( a1 < b1 )) && return 1
  (( a2 > b2 )) && return 0
  (( a2 < b2 )) && return 1
  (( a3 > b3 )) && return 0
  return 1
}

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <workspace> <plugin> <pre-baseline> <run-id>" >&2
    exit 2
  fi
  local workspace="$1" tile="$2" pre="$3" run_id="$4"

  # Positive-integer guard mirrors resolve-publish-run.sh's
  # validate_positive_int: ^[0-9]+$ would accept '0' which isn't a real
  # gh run id, contradicting the "positive integer" diagnostic below.
  if ! [[ "$run_id" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: <run-id> must be a positive integer, got: '${run_id}'" >&2
    exit 2
  fi
  if [[ -z "$pre" ]]; then
    echo "error: <pre-baseline> is empty — capture with 'tessl plugin info ${workspace}/${tile} | grep \"Latest Version\" | awk \"{print \\\$NF}\"' before merge" >&2
    exit 2
  fi

  # `gh run view --jq '.conclusion'` returns the literal string "null"
  # (not an empty string) when the run hasn't reached a terminal state
  # yet, and exits 0. Treat both empty AND "null" as "still in flight"
  # so the conjunction's "conclusion != success" branch can't mis-fire
  # on a pre-terminal run and report "publish failed" against a run
  # that hasn't actually finished. Callers should `gh run watch <id>`
  # before invoking this script; this guard catches the case where
  # they skipped that step.
  #
  # Capture stdout for the value and stderr to a separate tempfile so a
  # gh warning emitted on stderr can't get mixed into the conclusion
  # string and break the "conclusion == success" comparison. Combined
  # `2>&1` capture would otherwise let a single warning misclassify the
  # run on the happy path.
  local conclusion
  err_file=$(mktemp) || { echo "error: mktemp failed — cannot run verify-publish-landed.sh without writable TMPDIR" >&2; exit 2; }
  # Named handler ending `return 0`, not an inline `rm -f`: the EXIT trap's
  # final command status becomes the script's exit status, so an `rm` that
  # fails (unwritable TMPDIR) would rewrite this script's verdict into a
  # bare 1 — a publish-landed conjunction silently reported as a generic
  # failure. Cleanup reports on cleanup, never on the outcome.
  trap cleanup_err_file EXIT
  conclusion=$(gh run view "$run_id" --json conclusion --jq '.conclusion' 2>"$err_file") \
    || { local err; err=$(cat "$err_file"); echo "error: 'gh run view ${run_id}' failed: ${err} — verify (1) the run ID is correct (cross-check 'gh run list --workflow <publish-workflow-name> --branch main --limit 10'), (2) 'gh auth status' shows you're authenticated against the right host, then re-run; if the run failed at the GitHub side, inspect with 'gh run view ${run_id} --log-failed'" >&2; exit 2; }
  if [[ -z "$conclusion" || "$conclusion" == "null" ]]; then
    echo "error: 'gh run view ${run_id}' reports no terminal conclusion (got: '${conclusion}') — run is still in flight; run 'gh run watch ${run_id}' first, then re-run this script" >&2
    exit 2
  fi

  # Read the current latest version from the versions API. Separate stdout
  # (the JSON body) from stderr so a tessl warning can't poison the parsed
  # payload — same capture discipline as the gh call above and as
  # verify-moderation-cleared.sh. One page suffices: the endpoint returns the
  # newest versions first, a publish only ever adds a new highest version, so
  # the maximum is always on page 1 — no --paginate needed.
  local versions_endpoint="v1/tiles/${workspace}/${tile}/versions"
  local versions_json
  versions_json=$(tessl api "$versions_endpoint" 2>"$err_file") \
    || { local err; err=$(cat "$err_file"); echo "error: 'tessl api ${versions_endpoint}' failed: ${err} — verify (1) tessl CLI is installed and on PATH ('command -v tessl'), (2) the workspace/plugin slug is correct, (3) you have network access to the registry, then re-run 'tessl api ${versions_endpoint}' directly to inspect the failure before retrying the publish verification" >&2; exit 2; }

  # `jq empty`, not `jq -e .`: -e sets the exit from the last output's
  # truthiness, so a valid body evaluating to false/null would misreport as
  # "not JSON". `jq empty` parses and produces no output — exit reflects parse
  # validity alone (same reasoning as verify-moderation-cleared.sh).
  if ! printf '%s' "$versions_json" | jq empty >/dev/null 2>&1; then
    echo "error: 'tessl api ${versions_endpoint}' returned a body that is not valid JSON — the registry may be returning an error page or the endpoint shape changed; inspect it directly with 'tessl api ${versions_endpoint}' before retrying (body was: ${versions_json})" >&2
    exit 2
  fi

  # Max version across the page, ranked numerically. sort_by a per-element
  # parsed [major,minor,patch] key (jq compares arrays element-wise, so
  # [0,3,119] outranks [0,3,20]) and take the last — this depends only on the
  # `.version` string, not the endpoint's split-out int fields, so a shape
  # change there can't silently mis-rank. `tonumber? // 0` neutralises a
  # non-numeric component rather than aborting the whole rank.
  local current
  current=$(printf '%s' "$versions_json" | jq -r '
    [.data[]?.attributes.version | select(. != null)]
    | sort_by(split(".") | map(tonumber? // 0))
    | last // empty')
  if [[ -z "$current" ]]; then
    echo "error: 'tessl api ${versions_endpoint}' returned no published version — response carried no .data[].attributes.version entries; the endpoint shape may have changed or the plugin has never published. Inspect it directly with 'tessl api ${versions_endpoint}' (body was: ${versions_json})" >&2
    exit 2
  fi

  # Conjunct 1: this run must have concluded success. A failed conclusion
  # means our publish didn't land — any registry advance came from a
  # different, interleaved run.
  if [[ "$conclusion" != "success" ]]; then
    emit_and_exit "false" \
      "publish run ${run_id} concluded '${conclusion}', not 'success' — any registry advance from ${pre} to ${current} is attributable to interleaved publishes, not this run; inspect 'gh run view ${run_id} --log-failed' to diagnose" \
      "$conclusion" "$pre" "$current" 1
  fi

  # Conjunct 2: registry must have strictly advanced past the baseline.
  # Equality after a success conclusion means the workflow skipped the
  # publish step (conditional, no-op). Downgrade is impossible in
  # practice but guarded so the contract is total.
  if [[ "$current" == "$pre" ]]; then
    emit_and_exit "false" \
      "publish run ${run_id} concluded success but registry's Latest Version is still ${pre} — workflow exited cleanly without publishing (conditional skip or no-op publish step); inspect the run's job/step logs to confirm the publish step ran" \
      "$conclusion" "$pre" "$current" 1
  fi
  if ! version_gt "$current" "$pre"; then
    emit_and_exit "false" \
      "publish run ${run_id} concluded success but registry's Latest Version ${current} is not greater than baseline ${pre} — investigate the registry state" \
      "$conclusion" "$pre" "$current" 1
  fi

  emit_and_exit "true" \
    "publish landed: run ${run_id} = success and registry advanced ${pre} -> ${current}" \
    "$conclusion" "$pre" "$current" 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
