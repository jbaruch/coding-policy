#!/usr/bin/env bash
# Answer ONE question, definitively: is <version> published on the registry for
# <workspace>/<plugin>?
#
# Why this exists, separate from registry-version.sh: a publish can exit
# non-zero AFTER the artifact landed. The out-of-credits exit is one such case
# (rules/ci-safety.md "Credits Never Block Publishing"); a client-side publish
# TIMEOUT is another — the tessl CLI gives up after ~20s while the server
# finishes the upload, printing `✘ Failed to publish` for a publish that
# succeeded (observed: jbaruch/nanoclaw-admin run 32450781941, 0.1.497 created
# at 05:30:46 INSIDE that run's window). "Did the registry advance past a
# baseline" cannot tell those apart from an interleaved publish by someone
# else. "Does the EXACT version this run tried to publish exist" can.
#
# registry-version.sh answers "what is the latest" (the versions LIST endpoint).
# This answers "is this specific version there" (the versions/<v> endpoint), and
# is immune to an interleaved publish moving the latest past it.
#
# The API distinguishes the two answers cleanly (probed live against
# jbaruch/coding-policy):
#   present -> exit 0, body {"data":{"attributes":{"version":"x.y.z", …}}}
#   absent  -> exit 1, body {"error":{"title":"Not Found","status":404, …}}
# An auth or network failure ALSO exits non-zero, so a non-zero exit alone is
# not "absent" — the 404 in the body is the discriminator. Anything else is
# INDETERMINATE and exits 2: a caller that cannot tell must never claim the
# artifact landed (fail closed).
#
# jq is deliberately NOT used: smart-publish.sh, this script's caller, keeps to
# python3 for JSON so the publish path carries no undocumented jq dependency
# (rules/script-delegation.md Script Requirements).
#
# Usage: registry-has-version.sh <workspace> <plugin> <version>
# Out:   one JSON object on stdout — {"exists":true|false,"version":"x.y.z"}
#        Diagnostics go to stderr. On exit 2 stdout is empty.
# Exit:  0 definitive answer (exists true or false); 2 indeterminate (tessl or
#        python3 absent, tessl unreachable, unparseable body, or a 200 whose
#        version field does not match what was asked for) or a usage error.

set -euo pipefail

# Script-global, NOT a main() local: the EXIT trap fires after main() returns,
# when a main-local would be out of scope and `set -u` would turn cleanup into
# an unbound-variable failure (same reasoning as registry-version.sh).
RHV_ERR_FILE=""

# EXIT-trap cleanup. `return 0` is load-bearing: an EXIT trap's final command
# status becomes the script's exit status, so a failing `rm` would rewrite a
# deliberate `exit 2` (rules/error-handling.md Shell Error Handling). `if ! rm`
# rather than a bare `rm`: under `set -e` a failing rm would abort the handler
# before `return 0` runs, reintroducing the rewrite it exists to prevent.
cleanup_rhv_err_file() {
  if [[ -n "${RHV_ERR_FILE:-}" ]]; then
    if ! rm -f "$RHV_ERR_FILE"; then
      echo "registry-has-version.sh: warning: could not remove temp file ${RHV_ERR_FILE} — remove it by hand" >&2
    fi
    RHV_ERR_FILE=""
  fi
  return 0
}

# Emit the result object. python3, not printf interpolation: <version> is a
# caller-supplied argument, and a quote, a backslash, or a control character in
# it would produce invalid JSON on stdout — breaking the contract this script
# declares (rules/script-delegation.md — JSON-producing). A serializer escapes
# whatever it is handed.
#   $1 exists (true|false)  $2 version
emit_result() {
  python3 -c '
import json, sys
print(json.dumps({"exists": sys.argv[1] == "true", "version": sys.argv[2]}))' "$1" "$2"
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <workspace> <plugin> <version>" >&2
    exit 2
  fi
  local workspace="$1" plugin="$2" version="$3"
  if [[ -z "$workspace" || -z "$plugin" || -z "$version" ]]; then
    echo "error: <workspace>, <plugin>, and <version> are all required and must be non-empty — pass the exact version the publish attempted" >&2
    exit 2
  fi

  command -v tessl >/dev/null 2>&1 \
    || { echo "error: tessl CLI not found on PATH — install it ('npm i -g @tessl/cli') or add it to PATH, then re-run" >&2; exit 2; }
  command -v python3 >/dev/null 2>&1 \
    || { echo "error: python3 not found on PATH — required to parse the versions API response; install Python 3 and re-run" >&2; exit 2; }

  local endpoint="v1/tiles/${workspace}/${plugin}/versions/${version}"

  RHV_ERR_FILE=$(mktemp) \
    || { echo "error: mktemp failed — cannot run registry-has-version.sh without a writable TMPDIR" >&2; exit 2; }
  trap cleanup_rhv_err_file EXIT

  # Capture stdout (the JSON body) and stderr (tessl's CLI-update notice, which
  # rides on stderr on every call) SEPARATELY — a notice mixed into the body
  # would break the parse. `set +e`/`rc=$?`/`set -e` is the sanctioned explicit
  # exit-code capture (rules/error-handling.md): a non-zero exit here is
  # EXPECTED on the absent path and must be inspected, not aborted on.
  local body rc=0
  set +e
  body="$(tessl api "$endpoint" 2>"$RHV_ERR_FILE")"
  rc=$?
  set -e

  # Classify in python3: present (200 + matching version), absent (404 in the
  # error body), or indeterminate. Exit codes mirror this script's, so the
  # classification lives in ONE place rather than being re-derived in shell.
  #   0 -> exists, 3 -> absent, 4 -> indeterminate (with a reason on stderr)
  local cls_rc=0
  python3 -c '
import json, sys
body, rc, want = sys.argv[1], int(sys.argv[2]), sys.argv[3]
try:
    doc = json.loads(body) if body.strip() else None
except json.JSONDecodeError as e:
    print(f"body is not JSON ({e})", file=sys.stderr)
    sys.exit(4)
if rc == 0:
    got = (doc or {}).get("data", {})
    got = got.get("attributes", {}).get("version") if isinstance(got, dict) else None
    # A 200 whose version is not the one asked for means the endpoint did not
    # answer THIS question (a redirect to latest, a shape change). Refusing it
    # keeps a wrong artifact from ever reading as "landed".
    if got == want:
        sys.exit(0)
    print(f"read succeeded but the returned version is {got!r}, not {want!r}", file=sys.stderr)
    sys.exit(4)
status = None
if isinstance(doc, dict) and isinstance(doc.get("error"), dict):
    status = doc["error"].get("status")
if status == 404:
    sys.exit(3)
print(f"non-zero exit {rc} with no 404 in the body (error status {status!r})", file=sys.stderr)
sys.exit(4)
' "$body" "$rc" "$version" 2>>"$RHV_ERR_FILE" || cls_rc=$?

  local detail
  detail="$(tr '\n' ' ' <"$RHV_ERR_FILE")" || detail="(could not read the captured stderr)"

  case "$cls_rc" in
    0)
      emit_result true "$version"
      exit 0
      ;;
    3)
      emit_result false "$version"
      exit 0
      ;;
    *)
      echo "error: cannot determine whether ${workspace}/${plugin}@${version} exists — ${detail:-no detail captured} — verify (1) 'command -v tessl', (2) the workspace/plugin slug, (3) network access to the registry, then inspect 'tessl api ${endpoint}' directly. Treat this as NOT confirmed: a caller must never report a release landed on an indeterminate read" >&2
      exit 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
