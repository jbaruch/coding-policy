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
# step). So this script replaces that action: same auto-bump-from-registry +
# commit-back behavior, plus the output capture the gate needs.
#
# The credit signature is matched CONSERVATIVELY and only on a non-zero exit:
# an unmatched signature fails SAFE (the confirm gate reds an unrecognised
# failure), so a tessl wording change degrades to red, never to a wrongly-green
# release. The regex is the single top-of-file constant below.
#
# Usage: smart-publish.sh <mode> <plugin-path> <ref-name> <ref-type>
#   <mode>        auto-bump = publish --bump patch from the registry, then
#                 commit the resolved version back to the manifest (mirrors
#                 tesslio/patch-version-publish); as-is = publish the manifest
#                 version verbatim, no bump, no commit-back.
#   <plugin-path> directory to publish (usually '.'); the manifest is
#                 auto-detected under it (.tessl-plugin/plugin.json, then
#                 tile.json).
#   <ref-name>    GITHUB_REF_NAME — the branch the auto-bump commit is pushed
#                 to (HEAD:<ref-name>). Unused by as-is mode.
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

# The out-of-credits signature in `tessl plugin publish`'s output. Observed as
# `##[error]Out of credits` on a live fleet publish (jbaruch-travel-policy
# 0.7.59). Matched case-insensitively and ONLY on a non-zero exit. Conservative
# by design: a wording change stops matching and the failure stays RED (safe),
# never wrongly green. Update this one constant if tessl changes the wording.
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

# Commit the resolved version back to the manifest and push it to the branch —
# the auto-bump half of tesslio/patch-version-publish. Pushes HEAD:<ref-name>
# (not a bare `git push`, which fails on the detached HEAD actions/checkout
# leaves), falling back to a branch + PR when a direct push is blocked by
# branch protection. Diagnostics only; never changes the caller's exit code.
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

  # [skip ci] is non-negotiable: the bump commit lands on the protected branch
  # and would otherwise re-trigger this publish workflow (ci-safety Publish-
  # Pipeline Loop-Prevention Carve-Out). Pathspec-scoped so an unrelated staged
  # file is never swept into the bump commit.
  git commit -m "Bump ${name} to ${version} [skip ci]" -- "$manifest" >&2

  if git push origin "HEAD:${ref_name}" >&2; then
    echo "smart-publish: pushed the manifest bump to ${version} on ${ref_name}." >&2
    return 0
  fi

  # Direct push blocked (branch protection) — open a branch + PR instead, the
  # same fallback tesslio/patch-version-publish uses.
  local branch="tessl-bump-${version}"
  echo "smart-publish: direct push to ${ref_name} blocked — opening ${branch} + PR." >&2
  git checkout -b "$branch" >&2
  git push -u origin "$branch" >&2
  gh pr create \
    --title "Bump ${name} to ${version} [skip ci]" \
    --body "Auto-created by smart-publish — direct push to the protected branch was blocked." >&2
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

  # Choose the publish invocation. auto-bump distinguishes a first publish (no
  # prior version to --bump from) from an auth/network failure by the registry
  # query's 404 — treating the latter as a first publish would silently publish
  # unbumped. as-is always publishes the manifest version verbatim.
  local -a pub_args
  local first_publish=false
  if [[ "$mode" == "auto-bump" ]]; then
    local info_out
    if info_out="$(tessl plugin info "$name" 2>&1)"; then
      pub_args=(plugin publish --bump patch "$path")
    elif printf '%s\n' "$info_out" | grep -qi '404'; then
      pub_args=(plugin publish "$path")
      first_publish=true
    else
      echo "error: could not query the tessl registry for '${name}': ${info_out} — this is a pre-publish failure (auth/network), nothing was published. Fix the cause and re-run." >&2
      emit_json failure 1 "" false false
      exit 1
    fi
  else
    pub_args=(plugin publish "$path")
  fi

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

  # Credit signature ONLY on a non-zero exit: a low-credit warning printed
  # during a SUCCESSFUL publish must never read as the tolerated failure case.
  local credit_signature=false
  if [[ $rc -ne 0 ]] && grep -qiE "$CREDIT_SIGNATURE_REGEX" "$SP_LOG_FILE"; then
    credit_signature=true
  fi
  cleanup_sp_log

  if [[ $rc -ne 0 ]]; then
    echo "smart-publish: publish exited ${rc} (credit_signature=${credit_signature}). The confirm gate reconciles this against the registry." >&2
    emit_json failure "$rc" "" "$credit_signature" "$first_publish"
    exit "$rc"
  fi

  # Success. `tessl plugin publish` wrote the resolved version back to the
  # manifest on disk; read it for the result and the bump commit.
  local published_version
  published_version="$(manifest_field "$manifest" version)"
  echo "smart-publish: published ${name}@${published_version}." >&2

  if [[ "$mode" == "auto-bump" ]]; then
    commit_back "$manifest" "$name" "$published_version" "$ref_name" "$ref_type"
  fi

  emit_json success 0 "$published_version" false "$first_publish"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
