#!/usr/bin/env bash
# Usage: resolve-bookkeeping-stamp.sh <source-sha> <bound-publish-run-id>
# Run in a Git checkout. Accepts a bound jbaruch/coding-policy publish.yml run
# for main (push or workflow_dispatch).
# Reads the original publish job in attempt 1, including after an ACR-only retry,
# and fetches origin's main. It does not confirm the publisher or watch Tests.
# Exit 0 emits {"outcome":"pushed"|"noop","source_commit":"<sha>"}.
# Pushed means the logged stamp is a single-parent child of the source and on
# fetched main, even if the push command reported failure. An explicit no-op
# keeps the original source SHA, which must still be reachable from main.
# Exit 1 means the evidence is unavailable, ambiguous or inconsistent.
# Exit 2 means invalid arguments. Non-zero exits produce no JSON.
set -euo pipefail

fail() {
  echo "resolve-bookkeeping-stamp: $1" >&2
  exit 1
}

main() {
  if [[ $# != 2 || ! "$1" =~ ^[0-9a-f]{40}$ || ! "$2" =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: $0 <full-source-sha> <bound-publish-run-id>" >&2
    return 2
  fi
  local source_sha="$1" run_id="$2" repo=jbaruch/coding-policy attempt run job job_id log
  attempt="repos/$repo/actions/runs/$run_id/attempts/1"
  run=$(gh api --hostname github.com "$attempt" --jq '[.id,.path,.head_branch,.head_sha,.run_attempt,.event] | @tsv') ||
    fail "cannot read run $run_id attempt 1; restore Actions read access and retry the bound run"
  local binding="$run_id"$'\t.github/workflows/publish.yml\tmain\t'"$source_sha"$'\t1\t'
  if [[ "$run" != "${binding}push" && "$run" != "${binding}workflow_dispatch" ]]; then
    fail "run $run_id attempt 1 does not match publish.yml on main at $source_sha; check the bound publisher run"
  fi

  # The attempt-scoped endpoint binds jobs even if the optional job field is absent.
  job=$(gh api --hostname github.com --paginate "$attempt/jobs" --jq '.jobs[] | select(.name == "publish") | [.id,.run_id,.head_sha,(if has("run_attempt") then .run_attempt else 1 end)] | @tsv') ||
    fail "cannot list publish jobs for run $run_id attempt 1; restore Actions read access and retry"
  job_id=${job%%$'\t'*}
  if [[ ! "$job_id" =~ ^[1-9][0-9]*$ || "$job" != "$job_id"$'\t'"$run_id"$'\t'"$source_sha"$'\t1' ]]; then
    fail "run $run_id attempt 1 has no unique source-bound publish job; inspect that attempt's job list"
  fi

  # gh can choose the run archive from --attempt even with --job. Keep the
  # step prefix so diagnostics from other steps cannot prove a stamp or no-op.
  if ! log=$(gh run view --repo "github.com/$repo" --attempt 1 --job "$job_id" --log |
    awk -F '\t' '$1 == "publish" && $2 == "Stamp CHANGELOG and publish next patch" {
      sub(/^[0-9][0-9][0-9][0-9]-[^ ]+ /, "", $3)
      sub(/\r$/, "", $3)
      if ($3 ~ /^smart-publish: (pushing bookkeeping commit |manifest already at )/) print $3
    }'); then
    fail "cannot read the log for run $run_id attempt 1 job $job_id; restore access to that archive before retrying"
  fi

  local push_line='^smart-publish: pushing bookkeeping commit ([0-9a-f]{40}) to main\.$'
  local noop_line='^smart-publish: manifest already at [0-9]+\.[0-9]+\.[0-9]+, nothing to commit back\.$'
  local outcome commit parents
  if [[ "$log" =~ $push_line ]]; then
    outcome=pushed
    commit=${BASH_REMATCH[1]}
  elif [[ "$log" =~ $noop_line ]]; then
    outcome=noop
    commit=$source_sha
  else
    fail "run $run_id attempt 1 job $job_id has no unique bookkeeping result; inspect the original publish step log"
  fi

  if ! git fetch --no-tags "https://github.com/$repo.git" refs/heads/main >/dev/null 2>&1; then
    fail "cannot fetch $repo main; check the Git checkout and remote read access, then retry"
  fi
  if [[ "$outcome" == pushed ]]; then
    if ! parents=$(git --no-replace-objects rev-list --parents -n 1 "$commit" 2>/dev/null) ||
      [[ "$parents" != "$commit $source_sha" ]]; then
      fail "stamp $commit must have sole parent $source_sha; inspect the original publish attempt and local history"
    fi
  fi
  if ! git --no-replace-objects merge-base --is-ancestor "$commit" FETCH_HEAD; then
    fail "commit $commit is not proven on fetched $repo main; inspect the remote history before retrying"
  fi

  printf '{"outcome":"%s","source_commit":"%s"}\n' "$outcome" "$commit"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
