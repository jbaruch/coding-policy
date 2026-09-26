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
#           Nothing for a project without `agents.yaml`, or a throttled or
#           no-change run with no carve-out finding. A Herdr session, and a
#           portable (tessl) session, report findings but never update. In
#           git, the update runs
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
  # python3 first, then jq; printed only once a tool produced it.
  local out
  if command -v python3 >/dev/null && out="$(python3 -c 'import json, sys; print(json.dumps({"additionalContext": sys.argv[1]}))' "$1")"; then
    printf '%s\n' "$out"
    return 0
  fi
  if command -v jq >/dev/null && out="$(jq -n --arg c "$1" '{additionalContext: $c}')"; then
    printf '%s\n' "$out"
    return 0
  fi
  warn "neither python3 nor jq could encode the ACR status — install or repair python3 or jq, then start a new session; the status was: ${1}"
  return 0
}

#: Seconds allowed for the sync-proof fetch before the update is skipped.
FETCH_TIMEOUT_SEC="${ACR_LATEST_FETCH_TIMEOUT:-10}"
# Zero or a non-number would switch the bound off; fall back to the default.
[[ "$FETCH_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]] || FETCH_TIMEOUT_SEC=10

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

# Echo "source@requested" for each github:jbaruch/* dependency not at latest,
# comma-separated, from `acr list --json` output. Exit 0 checked (empty output =
# none pinned), 1 unreadable JSON, 2 no JSON tool.
pinned_jbaruch() { # <listing>
  # python3 first: exit 0 is its answer, 3 means the listing is unreadable (also
  # an answer); any other exit is python3 failing, and jq gets the question.
  local out rc=0
  if command -v python3 >/dev/null; then
    out="$(python3 -c '
import json, sys
try:
    deps = json.loads(sys.argv[1])["result"]["dependencies"]
    pinned = [d["declaration"]["source"] + "@" + str(d["declaration"].get("requested"))
              for d in deps
              if str(d["declaration"].get("source", "")).startswith("github:jbaruch/")
              and d["declaration"].get("requested") != "latest"]
except (ValueError, KeyError, TypeError, AttributeError):
    sys.exit(3)
sys.stdout.write(", ".join(pinned))
' "$1")" || rc=$?
    case "$rc" in
      0) printf '%s' "$out"; return 0 ;;
      3) return 1 ;;
    esac
  fi
  if command -v jq >/dev/null; then
    jq -e -r -j '[.result.dependencies[]
      | select((.declaration.source | tostring | startswith("github:jbaruch/")) and .declaration.requested != "latest")
      | "\(.declaration.source)@\(.declaration.requested)"] | join(", ")' <<<"$1" || return 1
    return 0
  fi
  return 2
}

# Build a bounded git network command in NET_CMD: timeout/gtimeout when
# present, otherwise git's own bounds (an HTTP low-speed limit and ssh
# connect/keepalive timeouts), so a stalled remote cannot hang session start.
bounded_git() { # <seconds> <git args...>
  local secs="$1"; shift
  if command -v timeout >/dev/null; then
    NET_CMD=(timeout "$secs" git "$@")
  elif command -v gtimeout >/dev/null; then
    NET_CMD=(gtimeout "$secs" git "$@")
  else
    NET_CMD=(env "GIT_SSH_COMMAND=${GIT_SSH_COMMAND:-ssh} -o ConnectTimeout=${secs} -o ServerAliveInterval=5 -o ServerAliveCountMax=2"
      git -c http.lowSpeedLimit=1000 -c "http.lowSpeedTime=${secs}" "$@")
  fi
}

# Echo why the checkout is not safe to update, or nothing when it is: freshly
# fetched from origin, HEAD containing origin's default branch, and a clean tree
# (rules/sync-before-work.md). A git failure is a reason, never a pass.
unsafe_reason() {
  local db status frc=0 head_line
  bounded_git "$FETCH_TIMEOUT_SEC" fetch --quiet origin
  "${NET_CMD[@]}" >/dev/null 2>&1 || frc=$?
  if (( frc != 0 )); then
    # git's message can carry a credential-bearing remote URL, so it stays out
    # of the status (rules/no-secrets.md Logging).
    printf 'fetching origin failed (exit %s); run %sgit fetch origin%s to see why' "$frc" '`' '`'
    return 0
  fi
  # Ask origin for its current default branch: a local origin/HEAD can be
  # stale after a remote rename, and a stale answer would vouch for nothing.
  local rc=0
  bounded_git "$FETCH_TIMEOUT_SEC" ls-remote --symref origin HEAD
  # git's stderr can carry a credential-bearing remote URL, so it is dropped
  # here and the failure below names the command to rerun (rules/no-secrets.md).
  head_line="$("${NET_CMD[@]}" 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    printf "asking origin for its default branch failed (exit %s); run %sgit ls-remote --symref origin HEAD%s to see why" "$rc" '`' '`'
    return 0
  fi
  local re='ref: refs/heads/([^[:space:]]+)[[:space:]]+HEAD' sha_re='(^|'$'\n'')([0-9a-f]{40,64})[[:space:]]+HEAD('$'\n''|$)' live_sha fetched_sha
  if [[ ! "$head_line" =~ $re ]]; then
    printf "origin did not name its default branch; run %sgit ls-remote --symref origin HEAD%s and set the remote's default branch" '`' '`'
    return 0
  fi
  db="${BASH_REMATCH[1]}"
  if [[ ! "$head_line" =~ $sha_re ]]; then
    printf "origin did not advertise the commit of its default branch; run %sgit ls-remote --symref origin HEAD%s and check the remote" '`' '`'
    return 0
  fi
  live_sha="${BASH_REMATCH[2]}"
  rc=0
  git show-ref --verify --quiet "refs/remotes/origin/${db}" || rc=$?
  case "$rc" in
    0) ;;
    1) printf 'origin/%s is missing after the fetch; check %sgit config --get-all remote.origin.fetch%s covers it, then run %sgit fetch origin%s' "$db" '`' '`' '`' '`'; return 0 ;;
    *) printf 'git failed reading origin/%s; run %sgit show-ref origin/%s%s to see why' "$db" '`' "$db" '`'; return 0 ;;
  esac
  # The fetched ref must be the tip origin advertises right now: a push between
  # the fetch and the ls-remote would otherwise vouch for a stale commit.
  if ! fetched_sha="$(git rev-parse --verify --quiet "refs/remotes/origin/${db}^{commit}")"; then
    printf 'git failed resolving origin/%s; run %sgit rev-parse origin/%s%s to see why' "$db" '`' "$db" '`'
    return 0
  fi
  if [[ "$fetched_sha" != "$live_sha" ]]; then
    printf 'origin/%s moved during the check; start a new session to check again' "$db"
    return 0
  fi
  rc=0
  git merge-base --is-ancestor "refs/remotes/origin/${db}" HEAD || rc=$?
  case "$rc" in
    0) ;;
    1) printf 'this checkout does not contain %sorigin/%s%s (behind or diverged); merge or rebase it onto %sorigin/%s%s first' '`' "$db" '`' '`' "$db" '`'; return 0 ;;
    *) printf 'git merge-base failed (exit %s) comparing HEAD with origin/%s; run %sgit merge-base --is-ancestor origin/%s HEAD%s to see why' "$rc" "$db" '`' "$db" '`'; return 0 ;;
  esac
  if ! status="$(git status --porcelain --untracked-files=all)"; then
    printf 'git status failed; run %sgit status%s to see why' '`' '`'
    return 0
  fi
  if [[ -n "$status" ]]; then
    printf 'the working tree has uncommitted changes; commit or stash them'
  fi
  return 0
}

main() {
  local root acr out rc=0 err reason in_git=1 diag

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
  # consumer `agents.yaml`) runs in every session, Herdr included: it only reads.
  local listing notes="" pinned checked=1 prc=0
  if ! listing="$("$acr" list --json --project "$root" 2>&1)"; then
    notes="NOTE: \`acr list --json\` failed, so the latest-specifier check did not run; run \`$(printf '%q' "$acr") list --json --project $(printf '%q' "$root")\` to see why, and reinstall acr if it keeps failing."
    checked=0
  else
    pinned="$(pinned_jbaruch "$listing")" || prc=$?
    case "$prc" in
      0) [[ -z "$pinned" ]] || notes="NOTE: agents.yaml pins jbaruch dependencies that must float at \`latest\` (Runtime-Managed Manifest Carve-Out, rules/dependency-management.md): ${pinned}. Set them to \`requested: latest\`." ;;
      2) notes="NOTE: neither python3 nor jq is on PATH, so the latest-specifier check did not run; install one of them."; checked=0 ;;
      *) notes="NOTE: \`acr list --json\` returned something unreadable, so the latest-specifier check did not run; run \`$(printf '%q' "$acr") list --json --project $(printf '%q' "$root")\` to inspect it, and upgrade acr (\`brew upgrade jbaruch/agentic-context-registry/acr\`) if its output format changed."; checked=0 ;;
    esac
  fi

  # Every Herdr session reports only: a worker never writes on the hook's say-so,
  # and the foreman never edits the shared checkout
  # (rules/agent-team-operation.md Writers and Checkouts).
  if [[ -n "${HERDR_ENV:-}" ]]; then
    [[ -z "$notes" ]] || emit "Session-start status — acr: ${notes}"
    return 0
  fi

  # An update the carve-out check could not vouch for is not made.
  if (( ! checked )); then
    emit "Session-start status — acr: ACR dependencies were not updated. ${notes}"
    return 0
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
    # The carve-out covers the lock only while it is gitignored: an update must
    # never create an unignored resolved-state file. check-ignore exits 1 for a
    # path no rule ignores; anything above 1 is a git failure.
    local irc=0
    git -C "$root" check-ignore -q .agents/registry.lock || irc=$?
    case "$irc" in
      0) ;;
      1)
        emit "Session-start status — acr: \`.agents/registry.lock\` is not gitignored here, so an update would create an unignored resolved-state file; ACR dependencies were not updated. Add \`.agents/\` to \`.gitignore\` per the Runtime-Managed Manifest Carve-Out (rules/dependency-management.md).${notes:+$'\n'}${notes}"
        return 0 ;;
      *)
        emit "Session-start status — acr: \`git check-ignore\` failed (exit ${irc}) for \`.agents/registry.lock\`, so ACR dependencies were not updated; run \`git -C $(printf '%q' "$root") status\` to see why."
        return 0 ;;
    esac
    if ! reason="$(cd "$root" && unsafe_reason)"; then
      emit "Session-start status — acr: could not enter $(printf '%q' "$root") to check it, so ACR dependencies were not updated; check the directory exists and is readable, then start a new session."
      return 0
    fi
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
