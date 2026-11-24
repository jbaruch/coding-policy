#!/usr/bin/env bash
# Usage: resolve-bookkeeping-stamp.sh <source-sha> <bound-publish-run-id>
# Run in a Git checkout. Accepts a bound jbaruch/coding-policy publish.yml run
# for main (push or workflow_dispatch).
# Reads the stamp-producing publish job's attempt log, including after an
# ACR-only retry, and fetches origin's main. It does not confirm the publisher
# or watch Tests.
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
  local source_sha="$1" run_id="$2" repo=jbaruch/coding-policy
  local run_path="repos/$repo/actions/runs/$run_id" run latest_attempt
  local binding="$run_id"$'\t.github/workflows/publish.yml\tmain\t'"$source_sha"$'\t'
  run=$(gh api --hostname github.com "$run_path" --jq '[.id,.path,.head_branch,.head_sha,.event,.run_attempt] | @tsv') ||
    fail "cannot read run $run_id; restore Actions read access and retry the bound run"
  latest_attempt=${run##*$'\t'}
  if [[ ! "$latest_attempt" =~ ^[1-9][0-9]*$ ||
    ( "$run" != "${binding}push"$'\t'"$latest_attempt" && "$run" != "${binding}workflow_dispatch"$'\t'"$latest_attempt" ) ]]; then
    fail "run $run_id does not match publish.yml on main at $source_sha with a valid attempt; check the bound publisher run"
  fi

  local step='Stamp CHANGELOG and publish next patch'
  local attempt job id job_binding job_id="" publish_attempt="" log
  for ((attempt = 1; attempt <= latest_attempt; attempt++)); do
    # The attempt-scoped endpoint binds jobs even if run_attempt is absent.
    job=$(gh api --hostname github.com --paginate "$run_path/attempts/$attempt/jobs" --jq "
      .jobs | if length == 0 then error(\"empty attempt job list\") else .[] end
      | select(.name == \"publish\")
      | [.steps[]? | select(.name == \"$step\")] as \$stamp
      | [.id,.run_id,.head_sha,(if has(\"run_attempt\") then .run_attempt else $attempt end),.status,
         (if (\$stamp | length) == 1 and \$stamp[0].status == \"completed\" then \$stamp[0].conclusion else null end)]
      | @tsv") ||
      fail "cannot list publish jobs for run $run_id attempt $attempt; inspect that attempt and restore Actions read access"
    # A retry can contain only ACR. The initial attempt must have a publisher.
    if [[ -z "$job" && "$attempt" != 1 ]]; then
      continue
    fi
    id=${job%%$'\t'*}
    job_binding="$id"$'\t'"$run_id"$'\t'"$source_sha"$'\t'"$attempt"$'\tcompleted\t'
    if [[ ! "$id" =~ ^[1-9][0-9]*$ || "$job" != "$job_binding"* || "$job" == *$'\n'* ]]; then
      fail "run $run_id attempt $attempt has no unique source-bound completed publish job; inspect that attempt's job list"
    fi
    case "${job#"$job_binding"}" in
      skipped) continue ;;
      success|failure|cancelled) ;;
      *) fail "run $run_id attempt $attempt job $id has no conclusive stamp step metadata; inspect its steps before retrying" ;;
    esac
    if [[ -n "$job_id" ]]; then
      fail "run $run_id attempts $publish_attempt and $attempt both have a publish job with an unskipped stamp step; inspect both attempts"
    fi
    job_id=$id
    publish_attempt=$attempt
  done
  if [[ -z "$job_id" ]]; then
    fail "run $run_id has no publish job that ran the stamp step; inspect the bound run before retrying"
  fi

  # Select both the archive and the job. Keep the step prefix so diagnostics
  # from other steps cannot prove a stamp or no-op.
  if ! log=$(gh run view --repo "github.com/$repo" --attempt "$publish_attempt" --job "$job_id" --log |
    awk -F '\t' -v step="$step" '$1 == "publish" && $2 == step {
      sub(/^[0-9][0-9][0-9][0-9]-[^ ]+ /, "", $3)
      sub(/\r$/, "", $3)
      if ($3 ~ /^smart-publish: (pushing bookkeeping commit |manifest already at )/) print $3
    }'); then
    fail "cannot read the log for run $run_id attempt $publish_attempt job $job_id; restore access to that archive before retrying"
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
    fail "run $run_id attempt $publish_attempt job $job_id has no unique bookkeeping result; inspect that publish step log"
  fi

  if ! git fetch --no-tags "https://github.com/$repo.git" refs/heads/main >/dev/null 2>&1; then
    fail "cannot fetch $repo main; check the Git checkout and remote read access, then retry"
  fi
  if [[ "$outcome" == pushed ]]; then
    if ! parents=$(git --no-replace-objects rev-list --parents -n 1 "$commit" 2>/dev/null) ||
      [[ "$parents" != "$commit $source_sha" ]]; then
      fail "stamp $commit must have sole parent $source_sha; inspect the selected publish attempt and local history"
    fi
  fi
  if ! git --no-replace-objects merge-base --is-ancestor "$commit" FETCH_HEAD; then
    fail "commit $commit is not proven on fetched $repo main; inspect the remote history before retrying"
  fi

  printf '{"outcome":"%s","source_commit":"%s"}\n' "$outcome" "$commit"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
