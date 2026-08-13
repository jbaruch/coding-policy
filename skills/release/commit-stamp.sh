#!/usr/bin/env bash
# Stage the stamped CHANGELOG and, when committing, self-push it to the branch.
# Extracted from the .github/actions/stamp-changelog run block so the git
# stage/commit/push branches are testable against a temporary remote (issue
# #284). The version stamping itself lives in stamp-changelog.py; this owns only
# the deterministic git side effects (rules/script-delegation.md).
#
# Usage: commit-stamp.sh <changelog> <do-commit> <commit-message> <ref-name> <ref-type>
#   do-commit   "true" commits + pushes; anything else stages only
#   ref-name    GITHUB_REF_NAME — the branch to push HEAD to
#   ref-type    GITHUB_REF_TYPE — must be "branch" to push (a tag ref would
#               push HEAD:<tag> and create a branch named after the tag)
# Out:  one JSON object on stdout describing the outcome —
#         {"outcome":"pushed","ref":"<name>"} | {"outcome":"noop"} |
#         {"outcome":"staged","changed":true|false}
#       progress and diagnostics go to stderr.
# Exit: 0 on pushed / noop / staged; non-zero on a non-branch ref, a bad
#       argument count, or a git failure.
set -euo pipefail

main() {
  if [ "$#" -ne 5 ]; then
    echo "usage: $0 <changelog> <do-commit> <commit-message> <ref-name> <ref-type>" >&2
    return 2
  fi
  local changelog="$1" do_commit="$2" commit_message="$3" ref_name="$4" ref_type="$5"

  if [ "$do_commit" != "true" ]; then
    # Stage only the changelog path so commit=false leaves the caller a staged
    # change to commit (the input's contract), never sweeping in an unrelated
    # dirty index. `changed` reports whether the stamp actually altered it.
    git add -- "$changelog"
    local changed=false
    if ! git diff --cached --quiet -- "$changelog"; then changed=true; fi
    echo "commit=false — staged $changelog for the caller (changed=$changed)." >&2
    python3 -c 'import json,sys; print(json.dumps({"outcome":"staged","changed":sys.argv[1]=="true"}))' "$changed"
    return 0
  fi

  # Refuse a non-branch ref before mutating anything: pushing HEAD:<ref-name> on
  # a tag ref would create a branch named after the tag. The stamp only makes
  # sense in a branch-triggered publish.
  if [ "$ref_type" != "branch" ]; then
    echo "error: stamp-changelog must push on a branch ref, got ref-type='${ref_type}' (ref '${ref_name}'). Run it in a branch-triggered workflow." >&2
    return 1
  fi

  # Loop-prevention is non-negotiable: the stamp commit MUST carry the CI-skip
  # marker or it re-triggers the publish workflow. Enforce it even when a caller
  # overrode `commit-message` without it.
  local msg="$commit_message"
  case "$msg" in
    *"[skip ci]"*) ;;
    *) msg="$msg [skip ci]" ;;
  esac

  # No working-tree or staged change to the changelog => nothing to stamp.
  # Capture to a variable so a `git status` failure aborts under set -e rather
  # than reading as an empty (no-op) result inside the test.
  local pending
  pending="$(git status --porcelain -- "$changelog")"
  if [ -z "$pending" ]; then
    echo "Nothing to stamp (top section already headed) — no commit, no push." >&2
    python3 -c 'import json; print(json.dumps({"outcome":"noop"}))'
    return 0
  fi

  git config user.name "github-actions[bot]"
  git config user.email "github-actions[bot]@users.noreply.github.com"
  # Commit ONLY the changelog path (pathspec) so an unrelated staged file is
  # never swept into the stamp commit and pushed.
  git commit -m "$msg" -- "$changelog" >&2

  # Always push the stamp commit ourselves rather than delegating to a
  # downstream bump commit. Delegating stranded the stamp whenever the publish
  # step exited non-zero AFTER publishing (issue #284) — the artifact shipped
  # but the bump/stamp push never ran. A blocked/failed push must surface — no
  # suppression (rules/error-handling.md).
  git push origin "HEAD:${ref_name}" >&2
  python3 -c 'import json,sys; print(json.dumps({"outcome":"pushed","ref":sys.argv[1]}))' "$ref_name"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
