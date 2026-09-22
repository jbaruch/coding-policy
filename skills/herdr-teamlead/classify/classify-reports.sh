#!/usr/bin/env bash
# Annotate a round's delivered reports in one call, so the lead gates them together.
#
# The lead reads every report body in full before gating
# (rules/agent-team-operation.md Reports), and that does not change. What
# changes is the turn structure: one call annotates every report, then the lead
# reads them all with their verdicts in hand and gates them in one turn, instead
# of spending a full-context turn per report (#482).
#
# An annotation is advisory. It never replaces the read, and a failed one never
# blocks gating: the report lands in `unannotated` with the reason, and the lead
# reads it exactly as it always has. A disagreement between the annotation and
# the lead's own reading is the case worth a second look.
#
# Usage: classify-reports.sh [--agent codex|claude|grok] <report>...
#
# Output contract (rules/script-delegation.md -- structured stdout):
#   stdout: one JSON object --
#     {"schema_version": 1, "agent": "<kind>" | "default",
#      "labels": [<classify-report.sh label>, ...],
#      "unannotated": [{"report": "<path>", "reason": "<diagnostic>"}, ...]}
#   `agent` is "default" when none was named and no label came back to show
#   which kind the default resolved to; each unannotated reason names it.
#   stderr: per-report progress.
#
# Exit 0 whenever the arguments were valid, including when some or every
# annotation failed -- those are reported, not fatal. Exit 2 on a usage error.
#
# Each report is one model call against the chosen vendor's quota.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd && printf x)"
HERE="${HERE%x}"
HERE="${HERE%$'\n'}"

SCRATCH=""
cleanup() { if [ -n "$SCRATCH" ]; then rm -rf "$SCRATCH"; fi; return 0; }
trap cleanup EXIT

die() { echo "classify-reports: $*" >&2; exit 2; }

main() {
  local agent="" reports=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --agent) agent="${2-}"; shift 2 || die "--agent needs codex, claude or grok" ;;
      -h|--help) sed -n '2,29p' "${BASH_SOURCE[0]}"; exit 0 ;;
      --*) die "unknown option '$1'" ;;
      *) reports+=("$1"); shift ;;
    esac
  done
  [ "${#reports[@]}" -gt 0 ] || die "usage: classify-reports.sh [--agent codex|claude|grok] <report>..."

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/classify-reports.XXXXXX")" || die "cannot create a temporary directory"
  SCRATCH="$work"

  local index=0 report
  for report in "${reports[@]}"; do
    index=$((index + 1))
    echo "  [${index}/${#reports[@]}] ${report}" >&2
    if bash "${HERE}/classify-report.sh" "$report" ${agent:+--agent "$agent"} \
         --out "${work}/label-${index}.json" >/dev/null 2>"${work}/err-${index}"; then
      :
    else
      printf '%s' "$report" > "${work}/failed-${index}"
    fi
  done

  python3 - "$work" "${#reports[@]}" "${agent:-default}" <<'PY'
import json, pathlib, sys
work, total, agent = pathlib.Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
labels, unannotated = [], []
for index in range(1, total + 1):
    label = work / "label-{}.json".format(index)
    failed = work / "failed-{}".format(index)
    if label.is_file():
        labels.append(json.loads(label.read_text(encoding="utf-8")))
    elif failed.is_file():
        lines = [line for line in (work / "err-{}".format(index)).read_text(encoding="utf-8",
                 errors="replace").splitlines() if line.strip()]
        unannotated.append({"report": failed.read_text(encoding="utf-8"),
                            "reason": lines[-1] if lines else "no diagnostic"})
print(json.dumps({"schema_version": 1, "agent": labels[0]["agent"] if labels else agent,
                  "labels": labels, "unannotated": unannotated}, sort_keys=True))
PY
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
