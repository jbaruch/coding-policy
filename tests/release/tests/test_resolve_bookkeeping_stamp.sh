#!/usr/bin/env bash
# Exercise the shipped CLI with raw GitHub response fixtures and real Git
# commits fetched from an isolated bare remote.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/../../../skills/release" && pwd)/resolve-bookkeeping-stamp.sh"
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/resolve-bookkeeping-test.XXXXXX")
cleanup() {
  if ! rm -rf "$TEST_ROOT"; then
    echo "warning: remove test fixtures at $TEST_ROOT" >&2
  fi
  return 0
}
trap cleanup EXIT

export GIT_AUTHOR_DATE='2025-01-01T00:00:00Z' GIT_COMMITTER_DATE='2025-01-01T00:00:00Z'
export GIT_ALLOW_PROTOCOL=file
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
RUN=142

gh() (
  cd "$BOOKKEEPING_FIXTURE"
  local route=repos/jbaruch/coding-policy/actions/runs/142
  case "$*" in
    "api --hostname github.com $route/attempts/1 --jq "*) jq -r "$6" run.json ;;
    "api --hostname github.com --paginate $route/attempts/1/jobs --jq "*) jq -r "$7" jobs.json ;;
    "run view --repo github.com/jbaruch/coding-policy --attempt 1 --job 573 --log")
      cat job.log
      exit "${LOG_EXIT:-0}"
      ;;
    *) echo "unexpected GitHub read: $*" >&2; exit 99 ;;
  esac
)
export -f gh

setup() {
  export BOOKKEEPING_FIXTURE="$TEST_ROOT/$1"
  mkdir -p "$BOOKKEEPING_FIXTURE"
  cd "$BOOKKEEPING_FIXTURE"
  git init --bare --quiet -b main remote.git
  git init --quiet -b main work
  cd work
  git config "url.file://$BOOKKEEPING_FIXTURE/remote.git.insteadOf" https://github.com/jbaruch/coding-policy.git
  git -c user.name=fixture -c user.email=fixture@example.invalid commit --quiet --allow-empty -m source
  SOURCE=$(git rev-parse HEAD)
  git push --quiet ../remote.git HEAD:main
  git -c user.name=fixture -c user.email=fixture@example.invalid commit --quiet --allow-empty -m stamp
  STAMP=$(git rev-parse HEAD)
  cat > ../run.json <<JSON
{"id":142,"path":".github/workflows/publish.yml","head_branch":"main","head_sha":"$SOURCE","run_attempt":1,"event":"push","status":"completed","conclusion":"success"}
JSON
  cat > ../jobs.json <<JSON
{"total_count":2,"jobs":[{"id":573,"name":"publish","run_id":142,"head_sha":"$SOURCE","run_attempt":1},{"id":574,"name":"acr","run_id":142,"head_sha":"$SOURCE","run_attempt":1}]}
JSON
  : > ../job.log
}

log_line() {
  printf 'publish\t%s\t2025-01-01T00:00:00.0000000Z %s\n' "$1" "$2" >> ../job.log
}

stamp_line() { log_line 'Stamp CHANGELOG and publish next patch' "smart-publish: pushing bookkeeping commit $1 to main."; }
noop_line() { log_line 'Stamp CHANGELOG and publish next patch' 'smart-publish: manifest already at 0.1.1, nothing to commit back.'; }
push_main() { git push --quiet ../remote.git HEAD:main; }

edit_fixture() {
  jq "$2" "../$1.json" > "../$1.new"
  mv "../$1.new" "../$1.json"
}

expect_result() {
  local result
  result=$(bash "$SCRIPT" "$SOURCE" "$RUN")
  jq -e --arg outcome "$1" --arg source "$2" '. == {outcome:$outcome,source_commit:$source}' <<< "$result" >/dev/null
}

expect_refusal() {
  local result rc=0
  result=$(bash "$SCRIPT" "$SOURCE" "$RUN" 2>../err) || rc=$?
  if [[ "$rc" != 1 || -n "$result" || "$(cat ../err)" != *"$1"* ]]; then
    echo "expected exit 1, no JSON and diagnostic containing '$1'; got exit $rc, stdout '$result'" >&2
    cat ../err >&2
    return 1
  fi
}

for scenario in pushed lost_reply noop acr_retry unavailable_run unavailable_log wrong_run absent_job \
  ambiguous_job wrong_attempt wrong_job_source missing_marker ambiguous_stamps conflicting_noop wrong_ref wrong_parent unlanded fetch_failure; do
  (
    setup "$scenario"
    case "$scenario" in
      pushed)
        log_line 'Run unit tests' 'smart-publish: manifest already at 0.1.1, nothing to commit back.'
        stamp_line "$STAMP"
        git -c user.name=fixture -c user.email=fixture@example.invalid commit --quiet --allow-empty -m later
        push_main
        expect_result pushed "$STAMP"
        ;;
      lost_reply)
        stamp_line "$STAMP"
        log_line 'Stamp CHANGELOG and publish next patch' 'error: git push reported failure.'
        edit_fixture run '.conclusion = "failure"'
        push_main
        expect_result pushed "$STAMP"
        ;;
      noop)
        noop_line
        edit_fixture run '.event = "workflow_dispatch"'
        expect_result noop "$SOURCE"
        ;;
      acr_retry)
        # The transport exposes only the original job and archive, even if the
        # latest attempt contains only ACR. The job row need not repeat the attempt.
        edit_fixture jobs 'del(.jobs[0].run_attempt)'
        stamp_line "$STAMP"
        push_main
        expect_result pushed "$STAMP"
        ;;
      unavailable_run) rm ../run.json; expect_refusal 'cannot read run' ;;
      unavailable_log) noop_line; LOG_EXIT=7 expect_refusal 'cannot read the log' ;;
      wrong_run) edit_fixture run '.path = ".github/workflows/tests.yml"'; expect_refusal 'does not match publish.yml' ;;
      absent_job) edit_fixture jobs 'del(.jobs[0])'; expect_refusal 'no unique source-bound publish job' ;;
      ambiguous_job) edit_fixture jobs '.jobs += [.jobs[0] | .id = 575]'; expect_refusal 'no unique source-bound publish job' ;;
      wrong_attempt) edit_fixture jobs '.jobs[0].run_attempt = 2'; expect_refusal 'no unique source-bound publish job' ;;
      wrong_job_source) edit_fixture jobs '.jobs[0].head_sha = "other"'; expect_refusal 'no unique source-bound publish job' ;;
      missing_marker) log_line 'UNKNOWN STEP' 'smart-publish: manifest already at 0.1.1, nothing to commit back.'; expect_refusal 'no unique bookkeeping result' ;;
      ambiguous_stamps) stamp_line "$STAMP"; stamp_line "$SOURCE"; expect_refusal 'no unique bookkeeping result' ;;
      conflicting_noop) stamp_line "$STAMP"; noop_line; expect_refusal 'no unique bookkeeping result' ;;
      wrong_ref) log_line 'Stamp CHANGELOG and publish next patch' "smart-publish: pushing bookkeeping commit $STAMP to other."; expect_refusal 'no unique bookkeeping result' ;;
      wrong_parent)
        git -c user.name=fixture -c user.email=fixture@example.invalid commit --quiet --allow-empty -m wrong-parent
        stamp_line "$(git rev-parse HEAD)"
        push_main
        expect_refusal 'must have sole parent'
        ;;
      unlanded)
        stamp_line "$STAMP"
        git update-ref refs/remotes/origin/main "$STAMP"
        expect_refusal 'not proven on fetched jbaruch/coding-policy main'
        ;;
      fetch_failure) noop_line; mv ../remote.git ../unavailable.git; expect_refusal 'cannot fetch jbaruch/coding-policy main' ;;
    esac
    echo "  pass: $scenario"
  )
done
