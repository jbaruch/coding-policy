#!/usr/bin/env bash
# Publish a tessl plugin, CAPTURING the publish command's output so a
# post-publish out-of-credits exit can be told apart from any other failure.
#
# Why this exists (rules/ci-safety.md "Credits Never Block Publishing"): a
# tessl org out-of-credits makes `tessl plugin publish` exit non-zero AFTER
# the artifact already published —
#   ✔ Published …@X  ->  ✔ Uploaded evals  ->  ##[error]Out of credits  ->  exit 1
# The release LANDED; the non-zero exit is a post-publish billing failure. The
# confirm gate tolerates that ONLY when it can NAME the failing step from the
# logs (the rule's requirement: "confirming the artifact landing AND naming the
# failing step from the logs"). A live org-credit-state proxy cannot — a
# genuine eval or bump-push failure during the same out-of-credits window looks
# identical. The out-of-credits SIGNATURE in the publish command's own output
# is the discriminator, and reading it requires OWNING the publish call rather
# than delegating to the opaque third-party `tesslio/patch-version-publish`
# composite action (a `uses:` step's stdout cannot be captured for a later
# step). So this script replaces that action: auto-bump on publish + a manifest
# commit-back, plus the output capture the gate needs. The bump is REGISTRY-
# aware (next = max(registry, manifest) + patch), not patch-version-publish's
# manifest-based `--bump patch`, which collides once a credit-outage run skips
# the commit-back and the manifest falls behind the registry.
#
# The credit signature is bound CAUSALLY to the exit, not matched anywhere in
# the log: it must BE the TERMINAL FAILURE line — the last SUBSTANTIVE line the
# process printed before exiting non-zero, after tessl's benign trailing
# continuation lines (a `→ https://tessl.io/pricing` link, blanks) are stripped.
# A DIFFERENT failure after the credit mention leaves its own error text as the
# terminal line and stays red (fail CLOSED); an early low-credit warning ahead of
# a different terminal failure likewise stays red. Only on a non-zero exit; no
# substantive line or a scan error also fails closed. A tessl wording change
# stops matching and degrades to red, never to a wrongly-green release. The
# credit text is the top-of-file constant below.
#
# Usage: smart-publish.sh <mode> <plugin-path> <ref-name> <ref-type>
#   <mode>        auto-bump = compute the next version REGISTRY-aware
#                 (max(registry, manifest) + patch), write it into the manifest,
#                 publish it, and commit the manifest back; as-is = publish the
#                 manifest version verbatim, no bump, no commit-back.
#   <plugin-path> directory to publish (usually '.'); the manifest is
#                 auto-detected under it (.tessl-plugin/plugin.json, then
#                 tile.json).
#   <ref-name>    GITHUB_REF_NAME — the protected branch the auto-bump commit is
#                 pushed to (HEAD:<ref-name>). A rejected push reds the run (a
#                 bump-push failure). Unused by as-is mode.
#   <ref-type>    GITHUB_REF_TYPE — commit-back only pushes on "branch"; a
#                 non-branch ref skips the bump-push with a warning (the publish
#                 still succeeded), never fails a landed release.
# Out:  ONE JSON object on stdout —
#         {"outcome":"success"|"failure","exit_code":N,
#          "version":"x.y.z"|null,"credit_signature":true|false,
#          "first_publish":true|false}
#       The publish command's own output and all diagnostics go to stderr (so
#       the CI log still shows the publish), keeping stdout a clean JSON line.
# Exit: the publish command's real exit code (0 success, non-zero failure);
#       2 on a usage error; 1 on a pre-publish setup failure (bad manifest,
#       registry unreachable) — nothing published, the confirm gate reds it.

set -euo pipefail

# Directory of this script — used to locate the sibling registry-version.sh.
_sp_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The out-of-credits signature. tessl's real out-of-credits tail is a multi-line
# block, observed live (fifty-tabs-of-fares 0.16.3, jbaruch-travel-policy 0.7.59):
#   ##[error]Out of credits. Upgrade at https://tessl.io/pricing
#   ✘ Your organization has run out of credits. …
#   → https://tessl.io/pricing            <- benign trailing link, NOT the failure
# Matched case-insensitively against the TERMINAL FAILURE line — the last
# SUBSTANTIVE output line, after tessl's benign trailing link/blank lines are
# stripped — never anywhere earlier. So neither an early credit warning nor a
# credit mention trailed by a DIFFERENT failure (its own error text, which
# becomes the terminal line) greens a genuine failure, while the real block above
# still matches on its `✘ …out of credits…` line. Conservative: a wording change
# stops matching and the failure stays RED (safe). Update this constant if tessl
# changes the wording; ONLY tessl's exact pricing link (optionally arrow-prefixed)
# is stripped as benign — every other URL stays substantive.
readonly CREDIT_SIGNATURE_REGEX='out of credits'

# Temp file for the captured publish output; script-global so the EXIT trap can
# clean it up after main() returns (a main-local would be out of scope and
# `set -u` would turn cleanup into an unbound-variable failure).
SP_LOG_FILE=""

# EXIT-trap cleanup. `return 0` is load-bearing: an EXIT trap's final command
# status becomes the script's exit status, so a failing `rm` here would rewrite
# the publish's real exit code (rules/error-handling.md Shell Error Handling).
# `if ! rm` rather than a bare `rm`: under `set -e` a failing rm would abort the
# handler before `return 0`; an `if` condition suspends `set -e`.
cleanup_sp_log() {
  if [[ -n "${SP_LOG_FILE:-}" ]]; then
    if ! rm -f "$SP_LOG_FILE"; then
      echo "smart-publish.sh: warning: could not remove temp file ${SP_LOG_FILE} — remove it by hand" >&2
    fi
    SP_LOG_FILE=""
  fi
  return 0
}

# Emit the one-line JSON result. python3 (not printf/jq) handles escaping and
# the null version, matching commit-stamp.sh (rules/script-authoring — shipped
# scripts produce JSON via python3, never an undocumented jq dependency).
#   $1 outcome  $2 exit_code  $3 version("" -> null)  $4 credit_signature  $5 first_publish
emit_json() {
  python3 -c '
import json, sys
outcome, exit_code, version, credit_sig, first = sys.argv[1:6]
print(json.dumps({
    "outcome": outcome,
    "exit_code": int(exit_code),
    "version": (None if version == "" else version),
    "credit_signature": credit_sig == "true",
    "first_publish": first == "true",
}))' "$1" "$2" "$3" "$4" "$5"
}

# Read a top-level string field from the plugin manifest with python3 (no jq
# dependency). Prints the value (empty if absent); returns non-zero only when
# the file is unreadable or not JSON — the caller decides what an empty field
# means.
manifest_field() {
  local manifest="$1" field="$2"
  python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
v = d.get(sys.argv[2])
print(v if isinstance(v, str) else "")' "$manifest" "$field"
}

# Compute the auto-bump target version, REGISTRY-aware. tessl `--bump patch`
# bumps from the (possibly stale) MANIFEST and fails with "already exists" once
# a credit-outage run skips the commit-back and the manifest falls behind the
# registry; computing from the registry never collides. Rules:
#   - registry empty (never published) -> the manifest version (first publish)
#   - manifest strictly ahead of registry (a human minor/major bump) -> manifest
#   - otherwise -> registry latest + one patch (always a free version)
# Prints the target; exits 3 if a present registry version is not numeric
# MAJOR.MINOR.PATCH (a tool/shape failure the caller reds).
compute_target_version() {
  local reg="$1" man="$2"
  python3 -c '
import sys
reg, man = sys.argv[1], sys.argv[2]
def parse(v):
    p = v.split(".")
    return tuple(int(x) for x in p) if len(p) == 3 and all(x.isdigit() for x in p) else None
if not reg:
    print(man); sys.exit(0)                 # first publish: manifest as-is
rp = parse(reg)
if rp is None:
    sys.exit(3)                             # registry version unparseable
mp = parse(man)
if mp is not None and mp > rp:
    print(man)                              # human bumped the manifest ahead
else:
    print(f"{rp[0]}.{rp[1]}.{rp[2] + 1}")   # registry latest + one patch
' "$reg" "$man"
}

# Write a version into the manifest, preserving all other formatting — a
# targeted swap of the `"version": "<current>"` value, not a json.dump rewrite
# (which would reflow the file and noise up the commit-back diff). Exits 4 if
# the manifest has no version, 5 if the value could not be located to replace.
write_manifest_version() {
  local manifest="$1" target="$2"
  python3 -c '
import json, re, sys
m, target = sys.argv[1], sys.argv[2]
text = open(m).read()
cur = json.loads(text).get("version")
if cur is None:
    sys.exit(4)
pat = re.compile(r"(\"version\"\s*:\s*\")" + re.escape(cur) + r"(\")")
new, n = pat.subn(lambda _m: _m.group(1) + target + _m.group(2), text, count=1)
if n != 1:
    sys.exit(5)
open(m, "w").write(new)
' "$manifest" "$target"
}

# Commit the resolved version back to the manifest and push it directly to the
# protected branch — the auto-bump half of tesslio/patch-version-publish.
# Pushes HEAD:<ref-name> (not a bare `git push`, which fails on the detached
# HEAD actions/checkout leaves). Returns non-zero when the push is REJECTED so
# the caller reds the run: a blocked bump-push is a post-publish failure and
# stays red (rules/ci-safety.md), never a PR carrying a check-suppressing
# `[skip ci]` commit onto a feature branch — that marker is sanctioned ONLY on
# the protected-branch bookkeeping commit (Publish-Pipeline Loop-Prevention
# Carve-Out), and re-bumping through a PR would loop the publish on merge.
commit_back() {
  local manifest="$1" name="$2" version="$3" ref_name="$4" ref_type="$5"

  if [[ "$ref_type" != "branch" ]]; then
    echo "smart-publish: ref-type='${ref_type}' is not a branch — skipping the manifest bump-push (the publish itself succeeded)." >&2
    return 0
  fi

  git config user.name "github-actions[bot]"
  git config user.email "github-actions[bot]@users.noreply.github.com"
  git add -- "$manifest"
  if git diff --cached --quiet -- "$manifest"; then
    echo "smart-publish: manifest already at ${version}, nothing to commit back." >&2
    return 0
  fi

  # [skip ci] is non-negotiable HERE and sanctioned ONLY here: the bump commit
  # lands on the PROTECTED branch and would otherwise re-trigger this publish
  # workflow (ci-safety Publish-Pipeline Loop-Prevention Carve-Out). Pathspec-
  # scoped so an unrelated staged file is never swept into the bump commit.
  git commit -m "Bump ${name} to ${version} [skip ci]" -- "$manifest" >&2

  if ! git push origin "HEAD:${ref_name}" >&2; then
    echo "error: published ${name}@${version} but the manifest bump-push to ${ref_name} was rejected — the protected branch likely blocks the github-actions bot. The artifact IS published (the registry advanced); land the manifest bump by hand, or set this repo to publish-mode: as-is. Surfacing a failed run per rules/ci-safety.md — a bump-push failure stays red, distinct from a tolerated out-of-credits exit." >&2
    return 1
  fi
  echo "smart-publish: pushed the manifest bump to ${version} on ${ref_name}." >&2
  return 0
}

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <mode> <plugin-path> <ref-name> <ref-type>" >&2
    exit 2
  fi
  local mode="$1" path="$2" ref_name="$3" ref_type="$4"

  case "$mode" in
    auto-bump|as-is) ;;
    *) echo "error: mode must be 'auto-bump' or 'as-is', got '${mode}'" >&2; exit 2 ;;
  esac

  command -v tessl >/dev/null 2>&1 \
    || { echo "error: tessl CLI not found on PATH — install it ('npm i -g @tessl/cli') or add it to PATH, then re-run" >&2; exit 2; }
  command -v python3 >/dev/null 2>&1 \
    || { echo "error: python3 not found on PATH — required to emit the JSON result; install Python 3 and re-run" >&2; exit 2; }

  # Auto-detect the manifest under the plugin path (plugin.json authoritative,
  # tile.json legacy) — same precedence as tesslio/patch-version-publish and
  # the publish-landed-gate action.
  local manifest
  if [[ -f "${path}/.tessl-plugin/plugin.json" ]]; then
    manifest="${path}/.tessl-plugin/plugin.json"
  elif [[ -f "${path}/tile.json" ]]; then
    manifest="${path}/tile.json"
  else
    echo "error: no plugin manifest under '${path}' (expected .tessl-plugin/plugin.json or tile.json) — pass the plugin directory as <plugin-path>" >&2
    exit 1
  fi

  local name manifest_version
  name="$(manifest_field "$manifest" name)" \
    || { echo "error: cannot read '${manifest}' as JSON — fix the manifest and re-run" >&2; exit 1; }
  manifest_version="$(manifest_field "$manifest" version)"
  if [[ -z "$name" || "$name" != */* ]]; then
    echo "error: '${manifest}' name is '${name}', expected '<workspace>/<plugin>'" >&2
    exit 1
  fi
  if [[ -z "$manifest_version" ]]; then
    echo "error: '${manifest}' is missing a .version field" >&2
    exit 1
  fi

  # Choose the version + publish invocation. Both modes publish the manifest
  # version VERBATIM (`tessl plugin publish <path>`); the difference is what the
  # manifest holds:
  #   as-is     -> the pre-computed manifest version untouched (fifty-tabs-style;
  #                no bump, no commit-back).
  #   auto-bump -> the REGISTRY-aware next version, computed here and written into
  #                the manifest before publishing. NOT tessl `--bump patch`,
  #                which bumps from the manifest and collides once a credit-outage
  #                run skips the commit-back and the manifest falls behind.
  #                registry-version.sh reads the authoritative versions API
  #                (immediate) and doubles as the first-publish / tool-failure
  #                discriminator: {"version":null} = never published (first
  #                publish, target = manifest); exit != 0 = auth/network failure.
  local -a pub_args
  local first_publish=false
  if [[ "$mode" == "auto-bump" ]]; then
    local workspace="${name%%/*}" plugin_slug="${name#*/}"
    local reg_json reg="" rc_reg=0
    reg_json="$(bash "${_sp_dir}/registry-version.sh" "$workspace" "$plugin_slug")" || rc_reg=$?
    if [[ $rc_reg -ne 0 ]]; then
      echo "error: could not read the registry latest for '${name}' (registry-version.sh exit ${rc_reg}) — a pre-publish auth/network failure; nothing was published. Fix the cause and re-run." >&2
      emit_json failure 1 "" false false
      exit 1
    fi
    reg="$(printf '%s' "$reg_json" | python3 -c 'import json,sys;v=json.load(sys.stdin).get("version");print(v if v else "")')"
    [[ -z "$reg" ]] && first_publish=true
    local target rc_t=0
    target="$(compute_target_version "$reg" "$manifest_version")" || rc_t=$?
    if [[ $rc_t -ne 0 ]]; then
      echo "error: could not compute the next version for '${name}' (registry='${reg:-<none>}', manifest='${manifest_version}') — the registry latest is not numeric MAJOR.MINOR.PATCH. Nothing was published; inspect the registry." >&2
      emit_json failure 1 "" false false
      exit 1
    fi
    echo "smart-publish: auto-bump target ${target} (registry=${reg:-<none>}, manifest=${manifest_version})." >&2
    if ! write_manifest_version "$manifest" "$target"; then
      echo "error: could not write version ${target} into '${manifest}' — nothing was published." >&2
      emit_json failure 1 "" false false
      exit 1
    fi
  fi
  pub_args=(plugin publish "$path")

  trap cleanup_sp_log EXIT
  SP_LOG_FILE="$(mktemp)" \
    || { echo "error: mktemp failed — cannot capture the publish output without a writable TMPDIR" >&2; exit 2; }

  # Run the publish, CAPTURING combined stdout+stderr while still streaming it
  # to the CI log. `set +e`/`rc=$?`/`set -e` is the sanctioned explicit
  # exit-code capture (rules/error-handling.md) — a bare pipeline under `set -e`
  # would abort before we read the publish's real code, and the whole point is
  # to keep going past a non-zero exit to inspect it.
  echo "smart-publish: running 'tessl ${pub_args[*]}' (mode=${mode}) …" >&2
  local rc=0
  set +e
  tessl "${pub_args[@]}" >"$SP_LOG_FILE" 2>&1
  rc=$?
  set -e
  cat "$SP_LOG_FILE" >&2

  # Credit signature ONLY on a non-zero exit, and bound to the TERMINAL FAILURE
  # line. tessl prints its out-of-credits error then a benign trailing
  # `→ https://tessl.io/pricing` link, so the terminal line is found by peeling
  # ONLY that EXACT pricing continuation (and trailing blanks) off the end — no
  # other URL is stripped, so an unrelated URL-only terminal failure stays
  # substantive and keeps the run RED. A DIFFERENT failure after the credit
  # mention (its own error text) is likewise the terminal line -> red. An empty
  # result fails closed (no signature); a python scan error warns to stderr and
  # also fails closed (rules/error-handling.md — a best-effort failure that
  # continues emits a warning, never nothing). python3 (already a dependency)
  # does the trailing-only strip precisely.
  local credit_signature=false
  if [[ $rc -ne 0 ]]; then
    local last_line="" scan_rc=0
    last_line="$(python3 -c '
import re, sys
# Benign TRAILING continuation only: a blank line, or the EXACT tessl pricing
# link (optionally arrow-prefixed). Nothing else is stripped.
benign = re.compile(r"^\s*$|^\s*(?:" + "→" + r"\s*)?" + re.escape("https://tessl.io/pricing") + r"\s*$")
lines = sys.stdin.read().splitlines()
while lines and benign.match(lines[-1]):
    lines.pop()
sys.stdout.write(lines[-1] if lines else "")
' < "$SP_LOG_FILE")" || scan_rc=$?
    if [[ $scan_rc -ne 0 ]]; then
      echo "smart-publish: warning: terminal-line scan failed (python3 exit ${scan_rc}) — the run stays RED (no credit signature); inspect the publish step's captured output in the run log by hand before retrying." >&2
      last_line=""
    fi
    if [[ -n "$last_line" ]]; then
      # Distinguish grep no-match (exit 1 = no signature, the run stays red)
      # from a grep TOOL failure (exit >1) — the latter warns and also fails
      # closed, never a silent "no signature" (rules/error-handling.md Shell
      # Error Handling: an expected non-result is not a tool failure).
      local match_rc=0
      printf '%s' "$last_line" | grep -qiE "$CREDIT_SIGNATURE_REGEX" || match_rc=$?
      if [[ $match_rc -eq 0 ]]; then
        credit_signature=true
      elif [[ $match_rc -ne 1 ]]; then
        echo "smart-publish: warning: credit-signature match failed (grep exit ${match_rc}) — the run stays RED (no credit signature); inspect the publish step's captured output in the run log by hand before retrying." >&2
      fi
    fi
  fi
  cleanup_sp_log

  if [[ $rc -ne 0 ]]; then
    echo "smart-publish: publish exited ${rc} (credit_signature=${credit_signature}). The confirm gate reconciles this against the registry." >&2
    emit_json failure "$rc" "" "$credit_signature" "$first_publish"
    exit "$rc"
  fi

  # Success. The manifest on disk already holds the published version — auto-bump
  # wrote the target into it before publishing, as-is never touched it (neither
  # path passes `--bump`, so tessl does not rewrite it); read it back for the
  # result and the bump commit.
  local published_version
  published_version="$(manifest_field "$manifest" version)"
  echo "smart-publish: published ${name}@${published_version}." >&2

  if [[ "$mode" == "auto-bump" ]]; then
    if ! commit_back "$manifest" "$name" "$published_version" "$ref_name" "$ref_type"; then
      # The artifact published, but the manifest bump-push was rejected — a
      # post-publish failure that stays red (the confirm gate sees advanced +
      # failure + no credit signature -> non-credit post-publish failure).
      emit_json failure 1 "$published_version" false "$first_publish"
      exit 1
    fi
  fi

  emit_json success 0 "$published_version" false "$first_publish"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
