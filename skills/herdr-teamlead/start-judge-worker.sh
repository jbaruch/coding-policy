#!/usr/bin/env bash
# Start the judge worker on the tier its plan names, and prove it came up on
# that tier before anything briefs it.
#
# Neither harness sets reasoning effort from inside a session: `claude
# --effort` and `codex -c model_reasoning_effort=` are launch flags, so
# applying a tier is a worker start rather than a keystroke. Setting the model
# alone silently resets effort to that model's default, which is why the
# banner check below demands BOTH.
#
# The banner line is identified by the `banner_pattern` the judge config
# declares, never guessed: each harness prints its own banner, and a
# transcript row that happens to name the model is not proof of how the worker
# was started.
#
# The tier is read from the plan document rather than the command line: the
# `judge` block in config.json is the single place a model swap happens, and
# `teamlead plan` echoes it into its output. A model typed here by hand would
# defeat that.
#
# Contract:
#   argv  : <plan-file> <pane> [kind]
#           plan-file  a `teamlead plan` document carrying a `judge` object.
#           pane       the herdr pane id to start the worker in.
#           kind       herdr agent kind; defaults to claude.
#   stdout: one JSON object on exit 0 only —
#           {"agent":"<n>","model":"<m>","effort":"<e>"|null,
#            "pane":"<p>","banner_verified":true}
#   stderr: diagnostics.
#   exit  : 0 started and the startup-banner line echoed the whole tier,
#           2 usage error, missing tool, or a plan with no usable judge tier,
#           3 `herdr agent start` failed,
#           4 the worker started and its banner did not echo the tier —
#             the dispatch is invalid; read the pane before retrying.
#   env   : HERDR_BIN overrides the herdr binary; the tests point it at a fake.
#           JUDGE_BANNER_LINES overrides how many pane lines are read (default
#           below).
set -euo pipefail

HERDR_BIN="${HERDR_BIN:-herdr}"

#: Pane rows the startup banner is looked for in. The banner prints within the
#: first screenful; a larger window only adds unrelated transcript.
JUDGE_BANNER_LINES="${JUDGE_BANNER_LINES:-40}"

warn() { printf 'start-judge-worker: %s\n' "$1" >&2; }

main() {
  if (( $# < 2 || $# > 3 )); then
    warn "usage: start-judge-worker.sh <plan-file> <pane> [kind]"
    return 2
  fi
  local plan_file="$1" pane="$2" kind="${3:-claude}"

  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to read the plan document"
    return 2
  fi
  if ! command -v "$HERDR_BIN" >/dev/null 2>&1; then
    warn "'${HERDR_BIN}' not found on PATH — install the herdr CLI (https://herdr.dev) or point HERDR_BIN at the binary"
    return 2
  fi
  if [[ ! -f "$plan_file" || ! -r "$plan_file" ]]; then
    warn "plan file '${plan_file}' is not a readable file — pass the path \`teamlead plan --roles judge\` wrote"
    return 2
  fi

  local tier rc=0
  tier="$(jq -er '
    .judge // error("plan has no `judge` object — re-run `teamlead plan --roles judge` against a config carrying a judge block")
    | (.agent // "") as $a
    | (.model // "") as $m
    | (.banner_pattern // "") as $b
    | if $a == "" then error("plan judge.agent is empty — name the worker in the config judge block")
      elif $m == "" then error("plan judge.model is empty — name the model in the config judge block")
      elif $b == "" then error("plan judge.banner_pattern is empty — declare the worker\u0027s startup-banner pattern in the config judge block")
      else "\($a)\u001f\($m)\u001f\(.effort // "")\u001f\($b)" end
  ' < "$plan_file" 2>&1)" || rc=$?
  if (( rc != 0 )); then
    warn "could not read a judge tier from '${plan_file}': ${tier}"
    return 2
  fi

  # Split on the unit separator, never a tab: tab is IFS whitespace, so `read`
  # collapses a run of them and an empty `effort` would shift the banner
  # pattern into the effort field.
  local agent model effort banner_pattern
  IFS=$'\x1f' read -r agent model effort banner_pattern <<<"$tier"

  # Effort is omitted, never passed empty: a model that accepts no effort flag
  # would take `--effort ''` as a flag with a missing value.
  local -a flags=(--model "$model")
  if [[ -n "$effort" ]]; then
    flags+=(--effort "$effort")
  fi

  # herdr's own diagnostic is captured rather than discarded: it names why the
  # start failed, and this script's message cannot reconstruct that.
  local errfile
  errfile="$(mktemp)" || { warn "could not create a temp file for herdr's diagnostics"; return 2; }
  # shellcheck disable=SC2064  # errfile is expanded now, on purpose.
  trap "rm -f '${errfile}'" RETURN

  rc=0
  "$HERDR_BIN" agent start "$agent" --kind "$kind" --pane "$pane" -- "${flags[@]}" \
    >/dev/null 2>"$errfile" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent start ${agent}\` failed (exit ${rc}): $(tr '\n' ' ' < "$errfile") — read the pane with \`${HERDR_BIN} pane read ${pane} --source visible\` and start it by hand"
    return 3
  fi

  local banner
  rc=0
  banner="$("$HERDR_BIN" pane read "$pane" --source visible --lines "$JUDGE_BANNER_LINES" 2>"$errfile")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} pane read ${pane}\` failed (exit ${rc}): $(tr '\n' ' ' < "$errfile") — the worker started but its tier is unproven, so the dispatch is invalid"
    return 4
  fi

  # The STARTUP BANNER has to carry the whole tier, on one line. A transcript
  # row that happens to name the model proves nothing about how the worker was
  # started, and a model on one row plus an effort on another is two
  # coincidences, not a banner. So: find the banner line first, then read the
  # tier off THAT line.
  local line banner_seen=0 found_model=0 verified=0
  while IFS= read -r line; do
    grep -Eq -- "$banner_pattern" <<<"$line" || continue
    banner_seen=1
    [[ "$line" == *"$model"* ]] || continue
    found_model=1
    if [[ -z "$effort" || "$line" == *"$effort"* ]]; then
      verified=1
      break
    fi
  done <<<"$banner"

  if (( verified == 0 )); then
    if (( banner_seen == 0 )); then
      warn "pane ${pane} showed no line matching the banner pattern '${banner_pattern}' in its first ${JUDGE_BANNER_LINES} lines — the worker may not have started; read the pane, or fix judge.banner_pattern if the harness changed its banner"
    elif (( found_model == 1 )); then
      warn "pane ${pane}'s startup banner named model '${model}' but not effort '${effort}' — setting the model alone resets effort to that model's default, so the dispatch is invalid"
    else
      warn "pane ${pane}'s startup banner did not name model '${model}' — the worker is not provably on its pinned tier; read the pane and start it by hand"
    fi
    return 4
  fi

  jq -n --arg a "$agent" --arg m "$model" --arg e "$effort" --arg p "$pane" \
    '{agent: $a, model: $m, effort: (if $e == "" then null else $e end),
      pane: $p, banner_verified: true}'
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
