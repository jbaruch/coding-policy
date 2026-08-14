#!/usr/bin/env bash
# Report whether a tessl org is currently blocked by an out-of-credits state.
#
# The confirm-publish-landed.sh gate tolerates a non-zero publish exit ONLY
# when the org is verifiably out of credits (rules/ci-safety.md "Credits Never
# Block Publishing"): an out-of-credits org exits the publish step non-zero
# AFTER `✔ Published`, and that post-publish billing failure must not red a run
# whose artifact shipped. A DIFFERENT post-publish failure (a genuine eval
# failure, a bump-push failure) that ALSO lands the artifact must stay red — so
# the gate needs a discriminator, and this script is it.
#
# The org credit state is queryable: `tessl org usage --org <org> --json`
# returns e.g.
#   {"plan":{...},"credits":{"state":"over_budget","blocked":true,
#                            "remaining":-9930,"overLimit":true,...}}
# `.credits.blocked == true` (equivalently `.credits.state == "over_budget"`)
# means the org is out of credits. This script keys off `.credits.blocked`.
#
# Usage: org-credit-blocked.sh <org>
#   <org>  the tessl org slug — the <workspace> half of the plugin manifest's
#          `<workspace>/<plugin>` name.
# Out:    one JSON object on stdout — {"blocked": true} or {"blocked": false}.
# Exit:   0 on a clean read (a real boolean verdict);
#         2 on tool/parse failure (tessl or jq absent, tessl unreachable,
#         non-JSON body, or a `.credits.blocked` that is missing or not a
#         boolean). A missing/malformed shape is a TOOL FAILURE, never a
#         silent "not blocked": reading a garbled body as unblocked would
#         fail-OPEN the gate — it would preserve red for a real credit outage,
#         inverting the tolerance this script exists to grant. Reserve exit 0
#         for a genuine boolean.

set -euo pipefail

# Script-global, NOT a main() local: the EXIT trap fires after main() returns,
# when a main-local would be out of scope and `set -u` would turn cleanup into
# an unbound-variable failure (same reasoning as registry-version.sh).
OCB_ERR_FILE=""

# EXIT-trap cleanup. `return 0` is load-bearing: an EXIT trap's final command
# status becomes the script's exit status, so a failing `rm` here would rewrite
# a deliberate `exit 2` (rules/error-handling.md Shell Error Handling). `if !
# rm` rather than a bare `rm`: under `set -e` a failing rm would abort the
# handler before `return 0` runs, reintroducing the rewrite the handler exists
# to prevent; an `if` condition suspends `set -e`.
cleanup_ocb_err_file() {
  if [[ -n "${OCB_ERR_FILE:-}" ]]; then
    if ! rm -f "$OCB_ERR_FILE"; then
      echo "org-credit-blocked.sh: warning: could not remove temp file ${OCB_ERR_FILE} — remove it by hand" >&2
    fi
    OCB_ERR_FILE=""
  fi
  return 0
}

main() {
  if [[ $# -ne 1 ]]; then
    echo "usage: $0 <org>" >&2
    exit 2
  fi
  local org="$1"

  command -v tessl >/dev/null 2>&1 \
    || { echo "error: tessl CLI not found on PATH — install it ('npm i -g @tessl/cli') or add it to PATH, then re-run" >&2; exit 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; exit 2; }

  trap cleanup_ocb_err_file EXIT

  OCB_ERR_FILE=$(mktemp) \
    || { echo "error: mktemp failed — cannot run org-credit-blocked.sh without a writable TMPDIR" >&2; exit 2; }

  # Capture stdout (the JSON body) and stderr (a tessl warning) SEPARATELY so a
  # warning line can't merge into the parsed payload and be misread as the
  # credit state — same capture discipline as registry-version.sh.
  local usage_json
  if ! usage_json=$(tessl org usage --org "$org" --json 2>"$OCB_ERR_FILE"); then
    local err; err=$(cat "$OCB_ERR_FILE")
    cleanup_ocb_err_file
    echo "error: 'tessl org usage --org ${org} --json' failed: ${err} — verify (1) tessl CLI is installed and on PATH ('command -v tessl'), (2) the org slug is correct, (3) you are logged in with network access ('tessl status'), then re-run 'tessl org usage --org ${org} --json' directly to inspect the failure" >&2
    exit 2
  fi
  cleanup_ocb_err_file

  # `jq empty` parses and produces no output — its exit reflects parse validity
  # alone (same reasoning as registry-version.sh: `-e` would conflate a false
  # value with a parse failure).
  if ! printf '%s' "$usage_json" | jq empty >/dev/null 2>&1; then
    echo "error: 'tessl org usage --org ${org} --json' returned a body that is not valid JSON — the API may be returning an error page or the shape changed; inspect it directly with 'tessl org usage --org ${org} --json' (body was: ${usage_json})" >&2
    exit 2
  fi

  # `.credits.blocked` MUST be a boolean. A missing or non-boolean value is a
  # tool/parse failure, not a licence to read the org as "not blocked" — see
  # the exit-2 rationale in the header.
  if ! printf '%s' "$usage_json" | jq -e '.credits.blocked | type == "boolean"' >/dev/null 2>&1; then
    echo "error: 'tessl org usage --org ${org} --json' response has no boolean .credits.blocked — a shape change or an error body, not a credit verdict; inspect it directly with 'tessl org usage --org ${org} --json' (body was: ${usage_json})" >&2
    exit 2
  fi

  local blocked
  blocked=$(printf '%s' "$usage_json" | jq -r '.credits.blocked')
  printf '{"blocked":%s}\n' "$blocked"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
