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
#           no-change run, and any Herdr session. In git, the update runs
#           only on a checkout freshly fetched, containing origin's default
#           branch, with a clean tree; otherwise the status says why it was
#           skipped and nothing changes.
#   stderr: diagnostics only.
#   exit  : always 0.
#   needs : acr >= ACR_MIN_VERSION (top of file); an older or unreadable
#           version gets a status and no run.
#   env   : ACR_BIN names the acr executable (default `acr`), as ACR's own hook does.
#           ACR_LATEST_FETCH_TIMEOUT bounds the sync-proof fetch (seconds).
set -euo pipefail

warn() { printf 'check-acr-latest: %s\n' "$1" >&2; }

emit() { # <status text>
  if command -v python3 >/dev/null; then
    if ! python3 -c 'import json, sys; print(json.dumps({"additionalContext": sys.argv[1]}))' "$1"; then
      warn "python3 failed to encode the ACR status — it was: ${1}"
    fi
  elif command -v jq >/dev/null; then
    if ! jq -n --arg c "$1" '{additionalContext: $c}'; then
      warn "jq failed to encode the ACR status — it was: ${1}"
    fi
  else
    warn "neither python3 nor jq is on PATH — cannot report the ACR status: ${1}"
  fi
  return 0
}

#: Seconds allowed for the sync-proof fetch before the update is skipped.
FETCH_TIMEOUT_SEC="${ACR_LATEST_FETCH_TIMEOUT:-10}"

#: Oldest acr whose `freshness run --project --policy install` contract this
#: hook relies on. Renewal: re-check at every acr minor release, and raise it
#: whenever the freshness flags, exit codes or output this hook reads change;
#: an older acr is reported and never run.
ACR_MIN_VERSION="0.2.0"

# 0 when <have> is at least <want> (dotted numeric), 1 when older, 2 when
# <have> is not a dotted version.
version_at_least() { # <have> <want>
  local have="$1" want="$2" i h w
  [[ "$have" =~ ^[0-9]+(\.[0-9]+)*$ ]] || return 2
  local -a H W
  IFS=. read -r -a H <<<"$have"
  IFS=. read -r -a W <<<"$want"
  for i in 0 1 2; do
    h="${H[$i]:-0}"; w="${W[$i]:-0}"
    (( 10#$h > 10#$w )) && return 0
    (( 10#$h < 10#$w )) && return 1
  done
  return 0
}

# Echo origin's default branch for the repo in the working directory. Return 1
# when none of origin/HEAD, origin/main, origin/master resolves, and 2 on a git
# failure: only git's own "absent" exit (1) moves on to the next candidate.
default_branch() {
  local ref cand rc=0
  ref="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD)" || rc=$?
  case "$rc" in
    0) printf '%s' "${ref#origin/}"; return 0 ;;
    1) ;;
    *) return 2 ;;
  esac
  for cand in main master; do
    rc=0
    git show-ref --verify --quiet "refs/remotes/origin/${cand}" || rc=$?
    case "$rc" in
      0) printf '%s' "$cand"; return 0 ;;
      1) ;;
      *) return 2 ;;
    esac
  done
  return 1
}

# Echo why the checkout is not safe to update, or nothing when it is: freshly
# fetched from origin, HEAD containing origin's default branch, and a clean tree
# (rules/sync-before-work.md). A git failure is a reason, never a pass.
unsafe_reason() {
  local -a fetch=(git fetch --quiet origin)
  local db status
  if command -v timeout >/dev/null; then
    fetch=(timeout "$FETCH_TIMEOUT_SEC" "${fetch[@]}")
  elif command -v gtimeout >/dev/null; then
    fetch=(gtimeout "$FETCH_TIMEOUT_SEC" "${fetch[@]}")
  fi
  local frc=0
  "${fetch[@]}" >/dev/null 2>&1 || frc=$?
  if (( frc != 0 )); then
    # git's message can carry a credential-bearing remote URL, so it stays out
    # of the status (rules/no-secrets.md Logging).
    printf 'fetching origin failed (exit %s); run %sgit fetch origin%s to see why' "$frc" '`' '`'
    return 0
  fi
  local rc=0
  db="$(default_branch)" || rc=$?
  case "$rc" in
    0) ;;
    1) printf "origin's default branch could not be resolved"; return 0 ;;
    *) printf "git failed resolving origin's default branch"; return 0 ;;
  esac
  rc=0
  git merge-base --is-ancestor "refs/remotes/origin/${db}" HEAD || rc=$?
  case "$rc" in
    0) ;;
    1) printf 'this checkout does not contain %sorigin/%s%s (behind or diverged); sync it first' '`' "$db" '`'; return 0 ;;
    *) printf 'git merge-base failed (exit %s) comparing HEAD with origin/%s' "$rc" "$db"; return 0 ;;
  esac
  if ! status="$(git status --porcelain --untracked-files=all)"; then
    printf 'git status failed'
    return 0
  fi
  if [[ -n "$status" ]]; then
    printf 'the working tree has uncommitted changes'
  fi
  return 0
}

main() {
  local root acr out rc=0 err reason in_git=1 diag
  # Every Herdr session is off limits: a worker never writes on the hook's
  # say-so, and the foreman never edits the shared checkout
  # (rules/agent-team-operation.md Writers and Checkouts).
  [[ -z "${HERDR_ENV:-}" ]] || return 0

  # Outside git is the one expected rev-parse failure; any other is a broken
  # repository, reported and never updated as if it were a plain directory.
  local errf
  if ! errf="$(mktemp)"; then
    warn "mktemp failed — cannot check this project for ACR updates; check TMPDIR"
    return 0
  fi
  rc=0
  root="$(git rev-parse --show-toplevel 2>"$errf")" || rc=$?
  err="$(cat "$errf")"
  if ! rm -f "$errf"; then
    warn "could not remove ${errf} — delete it by hand"
  fi
  if (( rc != 0 )); then
    case "$err" in
      *"or any of the parent directories"*) root="$PWD"; in_git=0 ;;
      *)
        if [[ -f "${PWD}/agents.yaml" ]]; then
          emit "Session-start status — acr: git cannot read this repository (${err//$'\n'/ }), so ACR dependencies were not updated; fix the repository, then start a new session."
        fi
        return 0 ;;
    esac
  fi
  rc=0
  [[ -f "${root}/agents.yaml" ]] || return 0

  acr="${ACR_BIN:-acr}"
  if ! command -v "$acr" >/dev/null; then
    emit "Session-start status — acr: this project declares ACR dependencies (agents.yaml) but \`${acr}\` is not on PATH, so they were not updated; install it with \`brew install jbaruch/agentic-context-registry/acr\` or set ACR_BIN."
    return 0
  fi

  local version
  if ! version="$("$acr" --version 2>&1)"; then
    emit "Session-start status — acr: \`${acr} --version\` failed (${version//$'\n'/ }), so ACR dependencies were not updated; reinstall acr."
    return 0
  fi
  version="${version%% *}"
  rc=0
  version_at_least "$version" "$ACR_MIN_VERSION" || rc=$?
  case "$rc" in
    0) ;;
    1)
      emit "Session-start status — acr: acr ${version} is older than ${ACR_MIN_VERSION}, the oldest this hook runs, so ACR dependencies were not updated; upgrade with \`brew upgrade jbaruch/agentic-context-registry/acr\`."
      return 0 ;;
    *)
      emit "Session-start status — acr: \`${acr} --version\` printed \`${version}\`, which is not a release version, so ACR dependencies were not updated; check which acr is on PATH and reinstall a release."
      return 0 ;;
  esac

  # The Runtime-Managed Manifest Carve-Out check (rules/dependency-management.md,
  # consumer `agents.yaml`): name every github:jbaruch/* dependency not at latest.
  local listing notes=""
  if listing="$("$acr" list --json --project "$root" 2>&1)"; then
    # shellcheck disable=SC2016  # Backticks are Markdown in the Python source, not shell expansions.
    notes="$(python3 -c '
import json, sys
try:
    deps = json.loads(sys.argv[1])["result"]["dependencies"]
except (ValueError, KeyError, TypeError):
    print("NOTE: `acr list --json` returned something unreadable; the latest-specifier check did not run.")
    sys.exit(0)
pinned = [d["declaration"]["source"] + "@" + str(d["declaration"].get("requested"))
          for d in deps
          if d.get("declaration", {}).get("source", "").startswith("github:jbaruch/")
          and d["declaration"].get("requested") != "latest"]
if pinned:
    print("NOTE: agents.yaml pins jbaruch dependencies that must float at `latest` (Runtime-Managed Manifest Carve-Out, rules/dependency-management.md): "
          + ", ".join(pinned) + ". Set them to `requested: latest`.")
' "$listing")" || notes="NOTE: the latest-specifier check failed to run (python3)."
  else
    notes="NOTE: \`acr list --json\` failed, so the latest-specifier check did not run."
  fi

  # Under `tessl hook run` the environment is stripped, so a Herdr session
  # cannot be ruled out: report, never write.
  if [[ "${SESSION_START_MODE:-}" == portable ]]; then
    emit "Session-start status — acr: this agent runs SessionStart through tessl, which hides the session's environment, so ACR dependencies were not updated here; run \`$(printf '%q' "$acr") freshness run --project $(printf '%q' "$root") --policy install\` when it is safe.${notes:+$'\n'}${notes}"
    return 0
  fi

  if (( in_git )); then
    local trc=0
    # --error-unmatch exits 1 for an untracked path (its expected message is
    # silenced); anything else is a git failure, reported and never updated.
    git -C "$root" ls-files --error-unmatch .agents/registry.lock >/dev/null 2>&1 || trc=$?
    if (( trc > 1 )); then
      emit "Session-start status — acr: \`git ls-files\` failed (exit ${trc}) checking whether \`.agents/registry.lock\` is tracked, so ACR dependencies were not updated; run \`git -C $(printf '%q' "$root") status\` to see why."
      return 0
    fi
    if (( trc == 0 )); then
      emit "Session-start status — acr: \`.agents/registry.lock\` is committed here, so updating it would be an unfocused dependency change; ACR dependencies were not updated. Untrack it (\`git rm --cached .agents/registry.lock\`, gitignore \`.agents/\`) per the Runtime-Managed Manifest Carve-Out (rules/dependency-management.md).${notes:+$'\n'}${notes}"
      return 0
    fi
    reason="$(cd "$root" && unsafe_reason)"
    if [[ -n "$reason" ]]; then
      emit "Session-start status — acr: ACR dependencies were not updated: ${reason}. Run \`$(printf '%q' "$acr") freshness run --project $(printf '%q' "$root") --policy install\` once it is.${notes:+$'\n'}${notes}"
      return 0
    fi
  fi

  out="$("$acr" freshness run --project "$root" --policy install 2>&1)" || rc=$?
  if (( rc != 0 )); then
    diag="$(printf '%q' "$acr") freshness run --project $(printf '%q' "$root") --policy install"
    emit "Session-start status — acr: updating ACR dependencies failed (exit ${rc}):"$'\n'"${out}"$'\n'"Run \`${diag}\` to diagnose it."
    return 0
  fi
  if [[ -z "${out//[[:space:]]/}" ]]; then
    [[ -z "$notes" ]] || emit "Session-start status — acr: ${notes}"
    return 0
  fi
  emit "Session-start status — acr:"$'\n'"${out}${notes:+$'\n'}${notes}"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if ! main "$@"; then
    warn "internal error — no ACR status this session; run 'bash ${BASH_SOURCE[0]}' directly to see why"
  fi
  exit 0
fi
